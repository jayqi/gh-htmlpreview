from collections.abc import Callable, Mapping
import os
import shlex
import subprocess
import sys
import threading
from typing import Protocol
from urllib.parse import urlsplit
import webbrowser


class Process(Protocol):
    def wait(self) -> int: ...


class LaunchError(Exception):
    """The browser couldn't be launched."""


def skip_reason(
    url: str, env: Mapping[str, str] = os.environ, platform: str = sys.platform
) -> str | None:
    """Explain why the default browser shouldn't be opened, or return None if it should."""
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        port = urlsplit(url).port
        return (
            "Not opening a browser in an SSH session. To view it from your machine, forward "
            f"the port with 'ssh -L {port}:127.0.0.1:{port} HOST' and open the URL above. "
            "Use -p to keep the same port."
        )
    if platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return "Not opening a browser because neither DISPLAY nor WAYLAND_DISPLAY is set."
    return None


def _gh_config_browser(run: Callable[..., subprocess.CompletedProcess[str]]) -> str | None:
    try:
        result = run(
            ["gh", "config", "get", "browser"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_launcher(
    env: Mapping[str, str] = os.environ,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Find the browser command the way gh does: GH_BROWSER, gh's browser config, BROWSER."""
    if launcher := env.get("GH_BROWSER", "").strip():
        return launcher
    if launcher := _gh_config_browser(run):
        return launcher
    return env.get("BROWSER", "").strip() or None


def open_url(
    url: str,
    launcher: str | None,
    popen: Callable[..., Process] = subprocess.Popen,
    default: Callable[[str], bool] = webbrowser.open,
) -> None:
    """Open the URL with a browser command, or the system default browser if it's None.

    A browser command is started without waiting for it to exit.
    """
    if launcher is None:
        try:
            opened = default(url)
        except webbrowser.Error as e:
            raise LaunchError(f"Couldn't open the default browser: {e}.") from e
        if not opened:
            raise LaunchError("No default browser found.")
        return
    try:
        args = shlex.split(launcher)
    except ValueError as e:
        raise LaunchError(f"Couldn't parse browser command {launcher!r}: {e}.") from e
    try:
        # A new session keeps Ctrl-C in the terminal from reaching the browser.
        process = popen([*args, url], stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        raise LaunchError(f"Couldn't run browser command {launcher!r}: {e.strerror or e}.") from e
    threading.Thread(target=process.wait, daemon=True).start()
