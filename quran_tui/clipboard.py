"""Multi-backend system-clipboard helper.

OSC-52 (the escape-sequence "ask the terminal to copy" protocol) is the
TUI-native way to write to the clipboard, but it's also the most fragile
path: tmux silently eats it unless ``set -g allow-passthrough on`` (or
``set-clipboard on``) is configured, some terminals ignore it for security
reasons, and Windows Terminal's behaviour has shifted across versions. The
"copy" toast claims success while the clipboard stays empty.

This module bypasses that mess by detecting the environment and calling
a real clipboard binary directly. Order of preference:

1. **WSL** → ``clip.exe`` (always present on Win10+, lands in Windows clip)
2. **Wayland** → ``wl-copy``
3. **X11**     → ``xclip`` (clipboard selection), then ``xsel``
4. **macOS**   → ``pbcopy``
5. **OSC-52**  → escape sequence fallback (works *if* the terminal cooperates)

Each backend returns ``(ok: bool, mechanism: str)`` so the caller can show
which path was used in the success toast — useful for debugging when a
user reports "copy doesn't work".
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def copy_text(text: str) -> tuple[bool, str]:
    """Copy ``text`` to the system clipboard. Returns (ok, mechanism)."""
    if not text:
        return False, "empty"

    for backend in _backends():
        ok, name = backend(text)
        if ok:
            return True, name
    return False, "no backend"


def _backends():
    if _is_wsl():
        yield _clip_exe
    if os.environ.get("WAYLAND_DISPLAY"):
        yield _wl_copy
    if os.environ.get("DISPLAY"):
        yield _xclip
        yield _xsel
    if platform.system() == "Darwin":
        yield _pbcopy
    yield _osc52


def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _run_pipe(cmd: list[str], text: str, *, encoding: str = "utf-8") -> bool:
    if not shutil.which(cmd[0]):
        return False
    try:
        proc = subprocess.run(
            cmd,
            input=text.encode(encoding),
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _clip_exe(text: str) -> tuple[bool, str]:
    # clip.exe reads stdin as the Windows OEM codepage (cp437/cp1252 on
    # US systems), which mangles non-ASCII UTF-8 — Arabic comes out as
    # cp437 glyphs ("╪º┘ä"). Encoding as UTF-16 (with BOM, which "utf-16"
    # produces by default) is the Windows-native clipboard format and
    # round-trips correctly through clip.exe.
    return _run_pipe(["clip.exe"], text, encoding="utf-16"), "clip.exe (WSL)"


def _wl_copy(text: str) -> tuple[bool, str]:
    return _run_pipe(["wl-copy"], text), "wl-copy"


def _xclip(text: str) -> tuple[bool, str]:
    return _run_pipe(["xclip", "-selection", "clipboard"], text), "xclip"


def _xsel(text: str) -> tuple[bool, str]:
    return _run_pipe(["xsel", "--clipboard", "--input"], text), "xsel"


def _pbcopy(text: str) -> tuple[bool, str]:
    return _run_pipe(["pbcopy"], text), "pbcopy"


def _osc52(text: str) -> tuple[bool, str]:
    """Emit OSC-52 to stdout. Whether the terminal acts on it is out of
    our hands — we report success if the write doesn't raise."""
    import base64

    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    seq = f"\x1b]52;c;{payload}\x07"
    try:
        sys.__stdout__.write(seq)
        sys.__stdout__.flush()
        return True, "OSC-52"
    except OSError:
        return False, "OSC-52"
