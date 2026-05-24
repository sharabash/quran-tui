"""One-shot installer for the Windows media-key bridge.

On WSL this:

1. Verifies AutoHotkey v2 is installed (offers to ``winget install`` it).
2. Copies the bundled AHK script to ``%APPDATA%/quran-tui/``.
3. Creates a Startup-folder shortcut so the bridge auto-launches at login.
4. Launches the bridge immediately so media keys are live right away.

The installed script falls back to the default Windows handler whenever
quran-tui isn't running — so existing Spotify / Edge / etc. behaviour is
preserved.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

SCRIPT_NAME = "quran-tui-mediakeys.ahk"
STARTUP_SHORTCUT_NAME = "quran-tui-mediakeys.lnk"


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _wsl_to_windows(path: str | Path) -> str:
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def _run_powershell(snippet: str, timeout: float = 20.0) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", snippet],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _windows_env(var: str) -> str | None:
    """Read a Windows env var (eg APPDATA) via cmd.exe — works from WSL."""
    try:
        out = subprocess.check_output(
            ["cmd.exe", "/c", f"echo %{var}%"], text=True, timeout=5
        ).strip().replace("\r", "")
    except Exception:
        return None
    if not out or out.startswith("%"):
        return None
    return out


def _windows_path_to_wsl(win_path: str) -> Path | None:
    try:
        return Path(
            subprocess.check_output(["wslpath", "-u", win_path], text=True).strip()
        )
    except Exception:
        return None


def find_autohotkey() -> tuple[str | None, str | None]:
    """Return (windows-path-as-wsl-path, version-tag) or (None, None)."""
    candidates = [
        ("/mnt/c/Program Files/AutoHotkey/v2/AutoHotkey64.exe", "v2-x64"),
        ("/mnt/c/Program Files/AutoHotkey/v2/AutoHotkey32.exe", "v2-x86"),
        ("/mnt/c/Program Files/AutoHotkey/AutoHotkey.exe", "v1"),
        ("/mnt/c/Program Files (x86)/AutoHotkey/AutoHotkey.exe", "v1-x86"),
    ]
    for path, tag in candidates:
        if Path(path).exists():
            return path, tag
    # User-local install via winget can land under LOCALAPPDATA.
    local_appdata = _windows_env("LOCALAPPDATA")
    if local_appdata:
        wsl_local = _windows_path_to_wsl(local_appdata)
        if wsl_local is not None:
            for name in (
                "Programs/AutoHotkey/v2/AutoHotkey64.exe",
                "Programs/AutoHotkey/AutoHotkey.exe",
            ):
                p = wsl_local / name
                if p.exists():
                    return str(p), "v2-user"
    return None, None


def find_winget() -> str | None:
    if shutil.which("winget.exe"):
        return "winget.exe"
    # Also check WindowsApps path explicitly.
    p = Path("/mnt/c/Windows/System32/winget.exe")
    return str(p) if p.exists() else None


def winget_install_autohotkey(on_status: Callable[[str], None]) -> bool:
    winget = find_winget()
    if winget is None:
        on_status(
            "winget not found. Install AutoHotkey v2 manually from "
            "https://www.autohotkey.com/ and rerun --install-mediakeys."
        )
        return False
    on_status("installing AutoHotkey via winget (may take a minute)…")
    try:
        proc = subprocess.run(
            [
                winget,
                "install",
                "--id",
                "AutoHotkey.AutoHotkey",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--silent",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:
        on_status(f"winget failed: {exc}")
        return False
    if proc.returncode != 0:
        on_status(
            f"winget exited {proc.returncode}: {proc.stdout.strip() or proc.stderr.strip()}"
        )
        return False
    on_status("AutoHotkey installed.")
    return True


def _bundled_script_path() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / SCRIPT_NAME


def install_script_to_appdata(
    on_status: Callable[[str], None],
) -> Path:
    """Copy the bundled AHK script to %APPDATA%/quran-tui/. Returns WSL path."""
    appdata = _windows_env("APPDATA")
    if not appdata:
        raise RuntimeError("couldn't resolve %APPDATA%")
    wsl_appdata = _windows_path_to_wsl(appdata)
    if wsl_appdata is None:
        raise RuntimeError(f"couldn't convert {appdata!r} to a WSL path")
    target_dir = wsl_appdata / "quran-tui"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / SCRIPT_NAME
    shutil.copy2(_bundled_script_path(), target)
    on_status(f"installed script to {target}")
    return target


def add_to_startup(
    script_wsl_path: Path,
    ahk_wsl_path: str,
    on_status: Callable[[str], None],
) -> None:
    """Create a Startup-folder shortcut that auto-launches the AHK script."""
    appdata = _windows_env("APPDATA")
    if not appdata:
        raise RuntimeError("couldn't resolve %APPDATA%")
    startup_win = (
        appdata
        + r"\Microsoft\Windows\Start Menu\Programs\Startup\\"
        + STARTUP_SHORTCUT_NAME
    )
    script_win = _wsl_to_windows(script_wsl_path)
    ahk_win = _wsl_to_windows(ahk_wsl_path)

    snippet = (
        '$ws = New-Object -ComObject WScript.Shell; '
        f'$lnk = $ws.CreateShortcut("{startup_win}"); '
        f'$lnk.TargetPath = "{ahk_win}"; '
        f'$lnk.Arguments = "`"{script_win}`""; '
        f'$lnk.WorkingDirectory = "{appdata}\\quran-tui"; '
        '$lnk.Save()'
    )
    code, _stdout, stderr = _run_powershell(snippet)
    if code != 0:
        raise RuntimeError(f"failed to create startup shortcut: {stderr or code}")
    on_status(f"added auto-start at login: {startup_win}")


def launch_script(
    script_wsl_path: Path,
    ahk_wsl_path: str,
    on_status: Callable[[str], None],
) -> None:
    """Spawn the AHK script via cmd.exe so it survives our process exit."""
    script_win = _wsl_to_windows(script_wsl_path)
    ahk_win = _wsl_to_windows(ahk_wsl_path)
    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            "start",
            "",
            ahk_win,
            script_win,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    on_status("media-key bridge is live.")


def install(
    *,
    auto_install_ahk: bool = True,
    add_startup: bool = True,
    launch_now: bool = True,
    on_status: Callable[[str], None] | None = None,
) -> int:
    """Top-level entry point. Returns a CLI-friendly exit code."""
    log = on_status or (lambda msg: print(f"quran-tui: {msg}", file=sys.stderr))

    if not _is_wsl():
        log(
            "media-key setup is currently WSL-specific. On native Linux, run "
            "`xbindkeys` or use your desktop's hotkey daemon to POST "
            "http://127.0.0.1:13937/<command>."
        )
        return 0

    ahk_path, version = find_autohotkey()
    if ahk_path is None:
        log("AutoHotkey v2 not found.")
        if not auto_install_ahk:
            log(
                "Install it from https://www.autohotkey.com/ or pass "
                "--install-mediakeys --auto-install-ahk."
            )
            return 1
        if not winget_install_autohotkey(log):
            return 1
        ahk_path, version = find_autohotkey()
        if ahk_path is None:
            log("AutoHotkey still not found after install — bailing out.")
            return 1
    log(f"found AutoHotkey ({version}) at {ahk_path}")

    try:
        script_path = install_script_to_appdata(log)
    except Exception as exc:
        log(f"failed to install script: {exc}")
        return 1

    if add_startup:
        try:
            add_to_startup(script_path, ahk_path, log)
        except Exception as exc:
            log(f"warning: couldn't add to startup ({exc})")

    if launch_now:
        try:
            launch_script(script_path, ahk_path, log)
        except Exception as exc:
            log(f"warning: couldn't launch right now ({exc})")

    log(
        "media keys will route to quran-tui when it's running, otherwise "
        "they behave normally."
    )
    return 0
