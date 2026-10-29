# IoT Gateway Monitor

Cross-platform desktop app: **download → double-click → Setup Wizard → provision gateway agent → Monitoring + Security + Gateways + Settings**.

No Docker on the operator PC. No WSL required on Windows. No Docker on the gateway. No Lab / fake-sensor panel in this build.

| Piece | Role |
|-------|------|
| **Desktop app** (Windows `.exe` / Linux AppImage) | Wizard, left-rail Monitoring / Gateways / Settings, Actions, Admin |
| **Local Loki + Prometheus** | App-managed native processes (downloaded once in-UI) |
| **Gateway agent** | One systemd service: Mosquitto (native) + decrypt → log + rule/ML IDS + alert shipper + metrics + mDNS |
| **Communication** | SSH for control/provision; MQTT + HTTP for data |

---

## Download & run

[**GitHub Releases — desktop-latest**](https://github.com/ngmibach/iot-gateway/releases/tag/desktop-latest)

| Platform | File | How to run |
|----------|------|------------|
| **Windows** | `IoTGatewayMonitor.exe` | Download → double-click (WSL not required). If SmartScreen says unrecognized: **More info → Run anyway** (sign via Admin → Code Signing for full trust). |
| **Ubuntu / Linux** | `IoTGatewayMonitor-x86_64.AppImage` | `chmod +x` once → double-click |

### First-run Wizard (all in the UI)

1. First launch is Setup Wizard only — install Loki/Prometheus and pick the LAN address.
2. **Install monitoring** — downloads Loki + Prometheus once and starts them (progress in the Wizard; no Docker). Then **Open Monitoring**.
3. Later launches open the left-rail app shell: **Monitoring** (Metric · Activities · Control Plane), **Security** (Alerts · Protection Settings), **Gateways** (Registration · Settings · Clean up), **Settings** (Remove · Admin).
4. Register gateways under **Gateways → Registration** (LAN scan, SSH pin/key, firewall on Windows or manual TCP 3100 on Linux, install agent).
5. **Settings → Remove** uninstalls the monitoring panel on this PC (and registered gateways).
6. **Security → Security Alerts** filters detections by gateway, severity, text, and time range. Marking an alert reviewed is stored in the current browser profile.
7. **Security → Protection Settings** enables/disables IDS, behavioral rules, or ML and tunes the alert threshold. Saving requires the gateway sudo password and restarts the gateway agent. Export the selected period as HTML, CSV, or JSON from Security Alerts.

Gateways provisioned before the IDS integration was added must be provisioned again to receive the IDS runtime and model. The default agent IDS analyzes decrypted sensor messages and local Mosquitto connection logs; HAProxy/TLS-specific signals are only available in the legacy Compose deployment. The current IDS emits alerts but does not automatically block devices.

Gateway and fake-sensor steps can wait; Monitoring install is enough for the PC-side stack.

App data: Linux `~/.local/share/iot-gateway-monitor/`, Windows `%APPDATA%\IoTGatewayMonitor\`.

## Project documentation

- [System guide for presentations and interviews](docs/PROJECT-SYSTEM-GUIDE.md)
- [AI and Overleaf thesis-report writing guide](docs/THESIS-REPORT-AI-OVERLEAF-GUIDE.md)
- [Windows executable build instructions](apps/desktop/WINDOWS-BUILD.md)

---

## Branches

| Branch | Role |
|--------|------|
| **`main`** | Integration + CI workflow |
| **`linux`** | Push → rebuild AppImage → `desktop-latest` |
| **`windows`** | Push → rebuild `.exe` → `desktop-latest` |

---

## Architecture (Docker-free)

```
Operator PC                         Gateway device
─────────────                       ──────────────
IoTGatewayMonitor                   iot-gateway-agent (systemd)
  Wizard / Actions / Monitoring       ├─ mosquitto (native apt, supervised)
  Loki :3100 (native process)         ├─ decrypt → sensor_data.log → rule/ML IDS
  Prometheus :9090 (native)           ├─ IDS alerts → Loki :3100
                                      ├─ GET :9139/metrics /v1/health
                                      └─ mDNS
```

Legacy `gateway/docker-compose.yaml` remains for power users only (`IOTGW_GATEWAY_BACKEND=compose`). The default Wizard path never asks for Docker.

---

## Advanced / legacy Compose lab

```shell
cd gateway && docker compose up -d   # optional; not required for the packaged app
```

Fake sensors are not part of the packaged UI; publish with any MQTT client to the agent on port 1883.
