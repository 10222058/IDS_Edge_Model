# IoT Gateway Monitor — desktop app

**Double-clickable** Windows `.exe` / Linux AppImage. Setup Wizard, Actions, Monitoring, Admin — **no Docker on this PC**, no Lab panel in this build.

## End users

Download from [Releases / desktop-latest](https://github.com/ngmibach/iot-gateway/releases/tag/desktop-latest).

| Platform | File | First run |
|----------|------|-----------|
| Windows | `IoTGatewayMonitor.exe` | If SmartScreen says the app is unrecognized: **More info → Run anyway**. The EXE carries product metadata + icon; full trust needs Admin → Code Signing. |
| Linux | `IoTGatewayMonitor-*.AppImage` | `chmod +x` once, then double-click. |

1. Choose **Monitoring only** (default) or **Full setup**.
2. Click **Install monitoring** — the app downloads Loki + Prometheus and starts them (progress is polled; no Docker).
3. Click **Open Monitoring** (or Finish → Done). Gateway SSH/agent steps stay optional until you pick Full setup.

## Maintainers

- Push `linux` → AppImage; push `windows` → `.exe` (workflow on `main`).
- Local: `bash apps/desktop/packaging/build_linux_appimage.sh` / `build_windows.sh`.

## Layout

```
apps/desktop/
  packaging/   # PyInstaller + AppImage/exe
  shell/       # wizard server + GUI
  ui/          # wizard / actions / monitoring / admin
```
