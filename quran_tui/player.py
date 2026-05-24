"""Async wrapper around mpv via its JSON IPC socket.

mpv runs headless (``--idle --no-video --no-terminal``) with its built-in
``ytdl_hook`` enabled so we can hand it ``music.youtube.com`` URLs directly
and let mpv resolve the audio stream through yt-dlp.

Commands are JSON lines with a ``request_id``; responses come back on the
same socket interleaved with unsolicited events. A single reader task
demuxes the stream into per-request futures and event callbacks.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

MIN_VOLUME = 0.0
MAX_VOLUME = 150.0


def clamp_volume(volume: float) -> float:
    """Clamp ``volume`` into mpv's safe range ``[0, 150]``."""
    if volume < MIN_VOLUME:
        return MIN_VOLUME
    if volume > MAX_VOLUME:
        return MAX_VOLUME
    return float(volume)


def build_mpv_args(socket_path: str, *, ytdl_binary: str = "yt-dlp") -> list[str]:
    """Build the argv (sans ``mpv`` itself) for an audio-only ytdl-enabled mpv."""
    return [
        f"--input-ipc-server={socket_path}",
        "--idle=yes",
        "--no-video",
        "--no-terminal",
        "--really-quiet",
        "--audio-display=no",
        "--gapless-audio=yes",
        "--no-input-default-bindings",
        "--ytdl=yes",
        "--ytdl-format=bestaudio/best",
        f"--script-opts=ytdl_hook-ytdl_path={ytdl_binary}",
    ]


class MpvError(RuntimeError):
    """Raised when the mpv subprocess or its IPC socket misbehave."""


class MpvPlayer:
    """High-level async controller for a single headless mpv process."""

    def __init__(
        self,
        *,
        mpv_binary: str = "mpv",
        ytdl_binary: str = "yt-dlp",
        socket_path: str | None = None,
    ) -> None:
        self._mpv_binary = mpv_binary
        self._ytdl_binary = ytdl_binary
        self._sock_path = socket_path or os.path.join(
            tempfile.gettempdir(), f"ytm-tui-mpv-{os.getpid()}.sock"
        )
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._event_listeners: list[EventCallback] = []
        self._read_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._proc is not None:
            return

        if os.path.exists(self._sock_path):
            try:
                os.unlink(self._sock_path)
            except OSError:
                pass

        mpv_path = shutil.which(self._mpv_binary) or self._mpv_binary
        argv = build_mpv_args(self._sock_path, ytdl_binary=self._ytdl_binary)

        spawn = asyncio.create_subprocess_exec
        self._proc = await spawn(
            mpv_path,
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        for _ in range(100):
            if os.path.exists(self._sock_path):
                break
            await asyncio.sleep(0.05)
        else:
            await self.quit()
            raise MpvError(f"mpv did not create IPC socket at {self._sock_path}")

        self._reader, self._writer = await asyncio.open_unix_connection(self._sock_path)
        self._read_task = asyncio.create_task(self._read_loop(), name="mpv-reader")

        await self._observe("time-pos", 1)
        await self._observe("duration", 2)
        await self._observe("pause", 3)
        await self._observe("volume", 4)

    async def quit(self) -> None:
        try:
            if self._writer is not None and not self._writer.is_closing():
                try:
                    await self._send_command(["quit"], expect_response=False)
                except Exception:
                    pass
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
        finally:
            if self._read_task is not None:
                self._read_task.cancel()
                try:
                    await self._read_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._read_task = None
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except (TimeoutError, ProcessLookupError):
                    try:
                        self._proc.kill()
                    except ProcessLookupError:
                        pass
                self._proc = None
            if os.path.exists(self._sock_path):
                try:
                    os.unlink(self._sock_path)
                except OSError:
                    pass

    async def load(self, url: str) -> None:
        await self._send_command(["loadfile", url, "replace"])

    async def play(self) -> None:
        await self._send_command(["set_property", "pause", False])

    async def pause(self) -> None:
        await self._send_command(["set_property", "pause", True])

    async def toggle_pause(self) -> None:
        await self._send_command(["cycle", "pause"])

    async def stop(self) -> None:
        await self._send_command(["stop"])

    async def seek(self, seconds: float, *, relative: bool = True) -> None:
        await self._send_command(
            ["seek", seconds, "relative" if relative else "absolute"]
        )

    async def set_volume(self, volume: float) -> None:
        await self._send_command(["set_property", "volume", clamp_volume(volume)])

    async def get_position(self) -> float | None:
        return await self._get_property_safe("time-pos")

    async def get_duration(self) -> float | None:
        return await self._get_property_safe("duration")

    async def is_paused(self) -> bool:
        result = await self._get_property_safe("pause")
        return bool(result) if result is not None else True

    async def get_volume(self) -> float:
        result = await self._get_property_safe("volume")
        return float(result) if result is not None else 100.0

    def add_event_listener(self, callback: EventCallback) -> None:
        self._event_listeners.append(callback)

    async def _observe(self, name: str, prop_id: int) -> None:
        await self._send_command(["observe_property", prop_id, name])

    async def _get_property_safe(self, name: str) -> Any:
        try:
            return await self._send_command(["get_property", name])
        except MpvError:
            return None

    async def _send_command(
        self, command: list[Any], *, expect_response: bool = True
    ) -> Any:
        if self._writer is None:
            raise MpvError("mpv not started")

        request_id = self._next_request_id
        self._next_request_id += 1
        payload = json.dumps({"command": command, "request_id": request_id}) + "\n"

        future: asyncio.Future[Any] | None = None
        if expect_response:
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future

        async with self._write_lock:
            try:
                self._writer.write(payload.encode("utf-8"))
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError) as exc:
                if future is not None:
                    self._pending.pop(request_id, None)
                raise MpvError(f"mpv connection broken: {exc}") from exc

        if future is None:
            return None
        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise MpvError(f"mpv did not respond to {command!r}") from exc

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                if "request_id" in msg and msg["request_id"] in self._pending:
                    future = self._pending.pop(msg["request_id"])
                    if msg.get("error") == "success":
                        future.set_result(msg.get("data"))
                    else:
                        future.set_exception(
                            MpvError(f"mpv error: {msg.get('error')}")
                        )
                elif "event" in msg:
                    await self._dispatch_event(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MpvError("mpv reader died"))
            self._pending.clear()

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        for cb in list(self._event_listeners):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                continue
