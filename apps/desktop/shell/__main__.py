"""python -m shell  → clickable Setup Wizard app (or CLI server).

Usage:
  PYTHONPATH=apps/desktop:apps/control-service python -m shell --gui
  PYTHONPATH=... python -m shell --no-browser --port 9138
  PYTHONPATH=... python -m shell --start-services   # API then wizard (native Monitoring)

``--gui`` (default when DISPLAY/Wayland is set and stdin is not a TTY pipe) starts
control-service + wizard and opens a real app window — no browser URL typing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import detect
from .launcher import ProcessManager, wait_http
from .paths import CONTROL_HOST, CONTROL_PORT, STREAMLIT_PORT, WIZARD_PORT
from .wizard_server import run_forever


def _want_gui_by_default(argv: list[str]) -> bool:
    """Prefer GUI when a desktop session is available and user passed no mode flags."""
    if any(
        a in argv
        for a in (
            "--gui",
            "--no-browser",
            "--start-services",
            "--detect-only",
            "--run-control-service",
            "--help",
            "-h",
        )
    ):
        return False
    if os.environ.get("IOTGW_FORCE_CLI", "").strip() in ("1", "true", "yes"):
        return False
    # Frozen Windows .exe: always GUI (no console / DISPLAY).
    if getattr(sys, "frozen", False):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run_control_service() -> int:
    """Child entry used by the frozen .exe / AppImage (no system Python)."""
    from .paths import control_service_dir, repo_root

    cs = str(control_service_dir())
    if cs not in sys.path:
        sys.path.insert(0, cs)
    os.environ.setdefault("IOTGW_REPO_ROOT", str(repo_root()))
    from api.__main__ import main as api_main  # type: ignore[import-not-found]

    api_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--run-control-service" in raw:
        return _run_control_service()

    parser = argparse.ArgumentParser(description="IoT Gateway Monitor — desktop app")
    parser.add_argument("--host", default=CONTROL_HOST)
    parser.add_argument("--port", type=int, default=WIZARD_PORT)
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open as a desktop app window (starts control-service + wizard)",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--start-services",
        action="store_true",
        help="Start control-service (:9137) before wizard (native Monitoring default)",
    )
    parser.add_argument(
        "--with-streamlit",
        action="store_true",
        help="Also start Streamlit (:8501); off by default in Phase-1",
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Print Docker/WSL detection JSON and exit",
    )
    parser.add_argument(
        "--run-control-service",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: frozen child process
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(raw)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.detect_only:
        import json

        print(json.dumps(detect.environment_snapshot(), indent=2))
        return 0

    if args.gui or _want_gui_by_default(raw):
        from .gui import run_gui

        return run_gui(
            host=args.host,
            port=args.port,
            start_control=True,
            with_streamlit=args.with_streamlit,
        )

    mgr: ProcessManager | None = None
    if args.start_services:
        mgr = ProcessManager()
        ctrl = mgr.start_control_service()
        if not wait_http(ctrl.url, timeout=45, path="/health"):
            print("control-service failed to become healthy", file=sys.stderr)
            mgr.stop_all()
            return 1
        print(f"control-service ready at {ctrl.url}")
        if args.with_streamlit:
            try:
                st = mgr.start_streamlit()
                if wait_http(st.url, timeout=90):
                    print(f"streamlit ready at {st.url}")
                else:
                    print(
                        "streamlit did not become ready (wizard still usable)",
                        file=sys.stderr,
                    )
            except FileNotFoundError as e:
                print(f"streamlit skip: {e}", file=sys.stderr)

    print(
        f"Setup Wizard → http://{args.host}:{args.port}/wizard.html\n"
        f"Actions      → http://{args.host}:{args.port}/actions.html\n"
        f"Monitoring   → http://{args.host}:{args.port}/monitoring.html\n"
        f"Control API  → http://{CONTROL_HOST}:{CONTROL_PORT}\n"
        f"Streamlit    → http://{CONTROL_HOST}:{STREAMLIT_PORT} (optional)\n"
        "Windows: native Loki/Prometheus (no WSL); firewall checklist for :3100 only."
    )
    try:
        run_forever(args.host, args.port, open_browser=not args.no_browser)
    finally:
        if mgr is not None:
            mgr.stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
