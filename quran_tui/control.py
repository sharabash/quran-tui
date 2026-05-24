"""Tiny HTTP control plane for external keybinding helpers.

ytm-tui runs as a TUI inside a terminal, which means OS-level media keys
(``Media_Play_Pause``, ``Media_Next``, etc.) never reach it — they go to
whoever Windows / Linux currently routes media-control events to (Spotify,
your browser, MPRIS players …). To bridge that gap, this module exposes a
minimalist HTTP endpoint over loopback. A keybinding helper on the host
(AutoHotkey on Windows, ``bind`` on Linux, etc.) issues a ``POST`` to
``http://127.0.0.1:<port>/<command>`` when a media key fires.

The server is stdlib-only — no Flask / aiohttp — and listens on
``127.0.0.1`` exclusively. Commands are looked up in a handler registry;
unknown paths get a 404 and never reach the app.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

CommandHandler = Callable[[], Awaitable[None]]


_STATUS_TEXT = {
    200: "OK",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    500: "Internal Server Error",
}


class ControlServer:
    """Loopback-only HTTP listener for keybinding-driven control commands."""

    def __init__(self, host: str = "127.0.0.1", port: int = 13937) -> None:
        self.host = host
        self.port = port
        self.handlers: dict[str, CommandHandler] = {}
        self._server: asyncio.Server | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    def register(self, command: str, handler: CommandHandler) -> None:
        """Register a coroutine that executes when ``POST /<command>`` arrives."""
        self.handlers[command.strip("/")] = handler

    def list_commands(self) -> list[str]:
        return sorted(self.handlers.keys())

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:
            pass
        self._server = None

    # --- internals --------------------------------------------------------

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            try:
                request_line = await asyncio.wait_for(
                    reader.readline(), timeout=2.0
                )
            except TimeoutError:
                await self._respond(writer, 400)
                return

            try:
                line = request_line.decode("utf-8", errors="replace").strip()
            except Exception:
                await self._respond(writer, 400)
                return

            parts = line.split(" ")
            if len(parts) < 2:
                await self._respond(writer, 400)
                return
            path = parts[1]

            # Drain remaining headers; we don't care about the body.
            while True:
                try:
                    hdr = await asyncio.wait_for(reader.readline(), timeout=2.0)
                except TimeoutError:
                    break
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break

            if path == "/" or path == "/commands":
                body = json.dumps(
                    {"commands": self.list_commands()}, separators=(",", ":")
                ).encode()
                await self._respond(writer, 200, body, "application/json")
                return

            command = path.lstrip("/").split("?", 1)[0]
            handler = self.handlers.get(command)
            if handler is None:
                body = json.dumps(
                    {
                        "error": "unknown command",
                        "available": self.list_commands(),
                    },
                    separators=(",", ":"),
                ).encode()
                await self._respond(writer, 404, body, "application/json")
                return

            try:
                await handler()
            except Exception as exc:
                body = json.dumps(
                    {"error": str(exc)}, separators=(",", ":")
                ).encode()
                await self._respond(writer, 500, body, "application/json")
                return
            await self._respond(writer, 204)
        except Exception:
            pass
        finally:
            try:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            except Exception:
                pass

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        reason = _STATUS_TEXT.get(status, "Status")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode()
        try:
            writer.write(head + body)
            await writer.drain()
        except Exception:
            pass
