"""Native desktop window for the Setup Wizard (no CLI required).

Launch order:
1. Start control-service (:9137) + wizard HTTP (:9138) in-process threads/children.
2. Open a frameless-ish app window pointed at the wizard URL.

Window backends (first that works):
- Embedded Chromium via Playwright (``--app=``) — works without system WebKit.
- System Chrome/Chromium/Edge with ``--app=``.
- ``pywebview`` (Qt/GTK) when available.
- Last resort: default OS browser tab.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .launcher import ProcessManager, wait_http
from .paths import CONTROL_HOST, CONTROL_PORT, WIZARD_PORT, data_dir, ensure_data_dir
from .wizard_server import serve

logger = logging.getLogger(__name__)

# Splash routes first-run → wizard, returning installs → monitoring shell.
SPLASH_URL = f"http://{CONTROL_HOST}:{WIZARD_PORT}/splash.html"
WIZARD_URL = SPLASH_URL


def _visible_error(message: str) -> None:
    """Surface a startup failure. The packaged .exe has no console."""
    print(message, file=sys.stderr)
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "IoT Gateway Monitor", 0x10)
    except Exception:  # noqa: BLE001
        logger.warning("could not show error dialog: %s", message)


def _popen_window(cmd: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Start a GUI browser. ``start_new_session`` is POSIX-only."""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


# Chromium/Edge create these under --user-data-dir while the profile is held.
# Windows Chrome uses ``lockfile``; Linux/macOS use the Singleton* names.
_CHROMIUM_LOCK_NAMES = (
    "lockfile",
    "SingletonLock",
    "SingletonSocket",
    "SingletonCookie",
)


def _browser_using_profile(profile: Path) -> bool | None:
    """Whether a Chromium-based browser still holds our user-data-dir.

    Uses profile lock files only. Do **not** spawn PowerShell/``ps`` here — the
    wait loop polls often and would open a console on every tick.
    ``None`` means the profile dir could not be read — do not shut down.
    """
    try:
        for name in _CHROMIUM_LOCK_NAMES:
            if (profile / name).exists():
                return True
        return False
    except OSError:
        return None


def _wait_while_app_window_open(
    browser: subprocess.Popen | None,
    profile: Path,
    *,
    startup_grace_s: float = 8.0,
    poll_s: float = 0.5,
) -> None:
    """Keep serving until the app-mode browser for ``profile`` has exited.

    Chromium/Edge often spawn a short-lived broker: ``browser.wait()`` returns
    while the real window stays open. If we shut down then, the UI shows
    ERR_CONNECTION_REFUSED on 127.0.0.1.
    """
    if browser is None:
        while True:
            time.sleep(1.0)

    deadline = time.time() + startup_grace_s
    saw_profile_browser = False
    while True:
        in_use = _browser_using_profile(profile)
        if in_use is True:
            saw_profile_browser = True
        elif in_use is False and saw_profile_browser:
            return

        launcher_done = browser.poll() is not None
        if (
            launcher_done
            and in_use is False
            and time.time() >= deadline
            and not saw_profile_browser
        ):
            # Broker exited and no browser ever attached to our profile.
            return
        time.sleep(poll_s)


def _local_lib_dir() -> Path:
    """Optional user-extracted Chrome runtime libs (no sudo): apps/desktop/.local-libs."""
    return Path(__file__).resolve().parents[1] / ".local-libs" / "usr" / "lib" / "x86_64-linux-gnu"


def _with_local_libs(env: dict[str, str]) -> dict[str, str]:
    lib = _local_lib_dir()
    if lib.is_dir():
        prev = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib}:{prev}" if prev else str(lib)
    return env


def _playwright_chrome() -> Optional[Path]:
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.is_dir():
        return None
    for chrome in sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
        if chrome.is_file() and os.access(chrome, os.X_OK):
            return chrome
    for chrome in sorted(cache.glob("chromium-*/chrome-win*/chrome.exe"), reverse=True):
        if chrome.is_file():
            return chrome
    for chrome in sorted(cache.glob("chromium-*/chrome-mac*/Chromium"), reverse=True):
        if chrome.is_file():
            return chrome
    return None


def _system_chrome() -> Optional[str]:
    names = (
        "google-chrome-stable",
        "google-chrome",
        "chromium-browser",
        "chromium",
        "msedge",
        "microsoft-edge",
        "brave-browser",
    )
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    # Windows common paths
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Microsoft/Edge/Application/msedge.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def _open_app_window(url: str) -> subprocess.Popen | None:
    """Open URL in an app-mode browser window (no address bar)."""
    profile = ensure_data_dir() / "chromium-profile"
    profile.mkdir(parents=True, exist_ok=True)
    args_common = [
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--window-size=1280,840",
    ]

    env = _with_local_libs(os.environ.copy())
    chrome = _playwright_chrome()
    if chrome is not None:
        logger.info("Opening app window with Playwright Chromium: %s", chrome)
        return _popen_window([str(chrome), *args_common], env)

    system = _system_chrome()
    if system:
        logger.info("Opening app window with system browser: %s", system)
        return _popen_window([system, *args_common], env)

    # pywebview fallback
    try:
        import webview  # type: ignore

        def _run() -> None:
            webview.create_window(
                "IoT Gateway Monitor",
                url,
                width=1100,
                height=800,
            )
            webview.start()

        threading.Thread(target=_run, daemon=True).start()
        logger.info("Opened pywebview window")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("pywebview unavailable: %s", exc)

    # Last resort — OS default browser (still no CLI for the user)
    import webbrowser

    webbrowser.open(url)
    logger.warning("Fell back to default browser tab for %s", url)
    return None


def run_gui(
    *,
    host: str = CONTROL_HOST,
    port: int = WIZARD_PORT,
    start_control: bool = True,
    with_streamlit: bool = False,
) -> int:
    """Start backend services and open the desktop app window. Blocks until exit."""
    ensure_data_dir()
    mgr = ProcessManager()
    httpd = None
    browser: subprocess.Popen | None = None

    try:
        if start_control:
            ctrl = mgr.start_control_service()
            if not wait_http(ctrl.url, timeout=45, path="/health"):
                _visible_error(
                    "control-service failed to become healthy on "
                    f"http://{CONTROL_HOST}:{CONTROL_PORT}"
                )
                mgr.stop_all()
                return 1
            if with_streamlit:
                try:
                    st = mgr.start_streamlit(control_url=ctrl.url)
                    wait_http(st.url, timeout=60)
                except FileNotFoundError as exc:
                    logger.warning("streamlit skipped: %s", exc)

        httpd = serve(host, port, open_browser=False)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        splash = f"http://{host}:{port}/splash.html"
        # Wait on a static page, not /api/wizard/env. Env probes Loki/Prometheus
        # and on a fresh PC closed ports are slow to refuse, so the windowed
        # .exe used to exit before any UI appeared. Splash then routes to the
        # wizard (first run) or monitoring shell (setup already complete).
        if not wait_http(f"http://{host}:{port}", timeout=20, path="/splash.html"):
            _visible_error("The setup wizard failed to start.")
            return 1

        profile = ensure_data_dir() / "chromium-profile"
        browser = _open_app_window(splash)
        print(f"IoT Gateway Monitor running — {splash}")
        print(f"App data: {data_dir()}")
        print("Close the app window or press Ctrl+C to quit.")

        # Stay alive while the real browser window uses our profile. Do not
        # trust browser.wait() alone — Chromium's broker often exits first.
        try:
            _wait_while_app_window_open(browser, profile)
        except KeyboardInterrupt:
            print("\nShutting down…")
        return 0
    finally:
        if browser is not None and browser.poll() is None:
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        mgr.stop_all()
