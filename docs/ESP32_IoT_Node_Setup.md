# ESP32 IoT Node Setup Flow (for IDS Gateway)

Author: IoT Security Project Team  
Date: April 2026  
Scope: Setup one ESP32 sensor node that sends normal MQTT telemetry to the Raspberry Pi IDS Gateway.

---

## 1. Goal and System Flow

Use this setup to create legitimate IoT traffic so your IDS can learn and validate normal behavior.

```text
[ESP32 Node] -> WiFi -> [Raspberry Pi MQTT Broker] -> [IDS Capture + Inference]
     |
  Generates mock sensor data every 15s
  (simulates realistic IoT telemetry)
```

Key outcome:
- ESP32 publishes mock telemetry every 15 seconds
- Pi receives MQTT traffic on port 1883
- IDS captures and learns this as normal baseline traffic
- Ready for attack testing afterward

---

## 2. Source Code Location (Separated from Docs)

All ESP32 code is now in a dedicated folder:

- Project root: `esp32_node/`
- Build config: `esp32_node/platformio.ini`
- Firmware file: `esp32_node/src/main.cpp`
- Arduino IDE sketch: `esp32_node/arduino_ide/esp32_iot_node/esp32_iot_node.ino`

This markdown file is documentation-only. No embedded firmware code is kept here.

---

## 3. Hardware Checklist

Minimal (for network testing only):
- 1x ESP32 Dev Board (ESP32-WROOM-32 or equivalent)
- USB cable (data cable, NOT just power)
- Stable 5V power source

**Note:** No DHT sensor required for baseline network testing. Firmware generates mock sensor data to simulate legitimate IoT traffic.

Optional (for future enhancement):
- DHT22 temperature/humidity sensor
- 4.7k ohm resistor (pull-up for DHT data line)

---

## 4. Software Setup (PC + Raspberry Pi)

### 4.1 PC (Firmware Build/Upload)

Install:
- VS Code + PlatformIO extension (recommended)
- USB serial driver (CH340/CP210x if needed)

PlatformIO workflow:
- Open the folder `esp32_node/`
- Build, Upload, Monitor (115200 baud)

Arduino IDE workflow:
- Open `esp32_node/arduino_ide/esp32_iot_node/esp32_iot_node.ino`
- Board: ESP32 Dev Module
- Install libraries: `PubSubClient` (Nick O'Leary)
- Upload and open Serial Monitor at 115200 baud

### 4.2 Raspberry Pi (Gateway Side)

Install and run MQTT broker:

```bash
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
sudo systemctl status mosquitto
```

**IMPORTANT: Configure MQTT to listen on all interfaces (not just localhost):**

```bash
# Edit mosquitto configuration
sudo nano /etc/mosquitto/mosquitto.conf

# Add or uncomment these lines (add to end of file):
listener 1883 0.0.0.0
protocol mqtt
allow_anonymous true

# If you see ACL lines, comment them out for lab testing:
# acl_file /etc/mosquitto/acl.conf
# include_dir /etc/mosquitto/conf.d

# Save: Ctrl+X → Y → Enter
# Restart broker:
sudo systemctl restart mosquitto
```

Verify broker configuration:

```bash
# Check listening port
ss -tuln | grep 1883
# Should show: 0.0.0.0:1883 (not just 127.0.0.1:1883)

# Check for authorization errors
sudo tail -f /var/log/mosquitto/mosquitto.log
# Should see "Client connected", NOT "not authorised"
```

---

## 5. Configure ESP32 Firmware

Open `esp32_node/src/main.cpp` and update the CONFIGURATION SECTION with your values:

If using Arduino IDE, edit the same values in:
- `esp32_node/arduino_ide/esp32_iot_node/esp32_iot_node.ino`

Required configuration values:

- `WIFI_SSID` - Your WiFi network name
- `WIFI_PASSWORD` - Your WiFi password
- `MQTT_SERVER` - IP of Raspberry Pi (example: `192.168.4.1`)
- `MQTT_PORT` - MQTT broker port (default: `1883`)
- `MQTT_CLIENTID` - Unique identifier (example: `esp32-node-01`)
- `MQTT_TOPIC_DATA` - Topic for sensor data (example: `sensor/esp32-01/data`)
- `MQTT_TOPIC_STATUS` - Topic for online status (example: `sensor/esp32-01/status`)
- `PUBLISH_INTERVAL` - Time between MQTT publishes in milliseconds (default: `15000`)

**Note:** Firmware v2.0+ generates **mock sensor data** (no physical DHT sensor required). It simulates realistic IoT telemetry:
- Temperature: 20-30°C range
- Humidity: 40-70% range
- Signal strength: WiFi RSSI value
- Publishes every 15 seconds (regular pattern for IDS baseline learning)

Recommended settings for first test:
- Keep publish interval at 15 seconds (good for testing without DHT hardware)
- Keep one node first, add more nodes later
- Don't change MQTT topics unless deploying multiple nodes

---

## 6. Upload and Verify Node

### 6.1 Upload Firmware

From `esp32_node/`:

```bash
platformio run -t upload
```

### 6.2 Open Serial Monitor

```bash
platformio device monitor --baud 115200
```

Expected logs:
- WiFi connected
- IP assigned
- MQTT connected
- Periodic publish messages every ~15s

---

## 7. Verify Integration with Gateway IDS

On Raspberry Pi, verify MQTT traffic:

```bash
mosquitto_sub -h 127.0.0.1 -t "sensor/#" -v
```

You should see:
- `sensor/esp32-01/status online`
- Repeated messages on `sensor/esp32-01/data`

Optional packet-level check:

```bash
sudo tcpdump -i wlan0 -n 'tcp port 1883'
```

### 7.1 Quick Check Commands on Raspberry Pi (Copy/Paste)

Use these commands in separate terminals to confirm data path end-to-end:

```bash
# Terminal A: verify broker is up
sudo systemctl status mosquitto --no-pager

# Terminal B: verify broker is listening on network interface
ss -tuln | grep 1883

# Terminal C: receive all ESP32 MQTT messages
mosquitto_sub -h 127.0.0.1 -t "sensor/#" -v

# Terminal D: observe packet flow from ESP32 IP to broker
sudo tcpdump -i wlan0 -n 'host 192.168.4.13 and tcp port 1883'

# Terminal E (optional): watch broker logs for connect/disconnect events
sudo tail -f /var/log/mosquitto/mosquitto.log
```

Expected results:

- `mosquitto_sub` shows `sensor/esp32-01/status online` and periodic `sensor/esp32-01/data` payloads
- `tcpdump` shows packets from `192.168.4.13` to `192.168.4.1:1883`
- Mosquitto log shows `New client connected ... esp32-node-01`
- No `not authorised` errors

If ESP32 IP changes, update the `tcpdump` host IP accordingly.

---

## 8. Acceptance Criteria

Setup is considered successful when all are true:

- ESP32 connects to WiFi within 10-20 seconds after boot
- MQTT session stays connected for at least 30 minutes
- Telemetry is published at stable interval (15s +/- 2s)
- Pi receives all expected topics
- IDS capture pipeline can observe this traffic as normal

---

## 9. Troubleshooting Quick Guide

ESP32 not detected:
- Change USB cable
- Reinstall USB serial driver
- Hold BOOT while uploading

WiFi not connecting:
- Re-check SSID/password in `main.cpp`
- Check AP/router signal strength

MQTT not connecting:
- Ensure mosquitto is running on Pi
- Confirm Pi IP is correct in `MQTT_SERVER`
- Confirm port 1883 is open

No data on Pi subscriber:
- Check topic names in firmware
- Check same network subnet
- Restart ESP32 and mosquitto service

---

## 10. Next Step for Security Test

After baseline node works, run 3-device test:

- Device 1: Raspberry Pi IDS Gateway
- Device 2: ESP32 node (normal traffic)
- Device 3: Separate attacker machine (scan/flood simulation)

This is the correct way to validate detection quality and false positives.
