"""Tests for the multi-backend clipboard helper.

We don't exercise real subprocesses (that'd write to the test runner's
clipboard, which is rude). Instead we monkey-patch ``_run_pipe`` and
verify the backend selection logic picks the right binary for the
environment.
"""

from __future__ import annotations

from quran_tui import clipboard


def test_empty_returns_false(monkeypatch) -> None:
    ok, mechanism = clipboard.copy_text("")
    assert not ok
    assert mechanism == "empty"


def test_wsl_prefers_clip_exe(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], text: str) -> bool:
        calls.append(cmd)
        return cmd[0] == "clip.exe"

    monkeypatch.setattr(clipboard, "_run_pipe", fake_run)
    monkeypatch.setattr(clipboard, "_is_wsl", lambda: True)
    monkeypatch.setenv("DISPLAY", "")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    ok, mechanism = clipboard.copy_text("hello")
    assert ok
    assert mechanism == "clip.exe (WSL)"
    assert calls and calls[0][0] == "clip.exe"


def test_x11_falls_through_to_xclip(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], text: str) -> bool:
        calls.append(cmd)
        return cmd[0] == "xclip"

    monkeypatch.setattr(clipboard, "_run_pipe", fake_run)
    monkeypatch.setattr(clipboard, "_is_wsl", lambda: False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    ok, mechanism = clipboard.copy_text("hello")
    assert ok
    assert mechanism == "xclip"


def test_no_backend_falls_back_to_osc52(monkeypatch) -> None:
    def fake_run(cmd: list[str], text: str) -> bool:
        return False

    monkeypatch.setattr(clipboard, "_run_pipe", fake_run)
    monkeypatch.setattr(clipboard, "_is_wsl", lambda: False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    # OSC-52 just writes to stdout — capturing isn't worth the contortion.
    # Verify it returns True with the OSC-52 mechanism string.
    ok, mechanism = clipboard.copy_text("hello")
    assert ok
    assert mechanism == "OSC-52"
