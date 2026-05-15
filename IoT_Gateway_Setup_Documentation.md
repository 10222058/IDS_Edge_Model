# IoT Gateway Setup Documentation
## Raspberry Pi Configuration as Edge Gateway

**Document Version:** 1.1  
**Date:** April 7, 2026  
**Platform:** Raspberry Pi (Debian 13 - Trixie)  
**Kernel:** Linux 6.12.75+rpt-rpi-v8 (aarch64)

---

## 1. Executive Summary

This document provides a comprehensive engineering guide for configuring a Raspberry Pi as an **IoT Gateway** for intrusion detection systems (IDS). The gateway acts as a network boundary device between IoT sensor nodes and upstream networks, enabling packet capture and traffic analysis for anomaly detection.

### Key Objectives
- Transform Raspberry Pi into a multi-interface gateway
- Enable WiFi Access Point (AP) for IoT node connectivity
- Configure packet forwarding and Network Address Translation (NAT)
- Prepare the platform for IDS model inference
- Establish baseline traffic monitoring capabilities

---

## 2. System Architecture

### 2.1 Network Topology

```
┌─────────────────┐
│   Laptop        │
│ 192.168.137.1   │
│  (Ethernet)     │
└────────┬────────┘
         │ eth0 (192.168.137.0/24)
         │
    ┌────▼─────────────────────────────┐
    │   Raspberry Pi Gateway            │
    │  ┌──────────────┬─────────────┐   │
    │  │ eth0: UP             │ wlan0: AP   │   │
    │  │ 192.168.137.100/24   │ 192.168.4.1 │   │
    │  │ (uplink)             │ (Wi-Fi)     │   │
    │  └──────────────┴─────────────┘   │
    │  - hostapd: WiFi AP               │
    │  - dnsmasq: DHCP server           │
    │  - iptables: NAT/Forwarding       │
    └────┬───────────────────────────────┘
         │ wlan0 (192.168.4.0/24)
         │
    ┌────▼──────────────────────────────┐
    │  IoT Nodes (ESP32/NodeMCU)        │
    │  DHCP: 192.168.4.10-50            │
    │  - Sensor 1, 2, 3...              │
    └─────────────────────────────────── ┘
```

### 2.2 Interface Roles

| Interface | Subnet | Role | GW IP | Purpose |
|-----------|--------|------|-------|---------|
| **eth0** | 192.168.137.0/24 | Uplink | 192.168.137.1 | Connection to laptop/upstream |
| **wlan0** | 192.168.4.0/24 | Access Point | 192.168.4.1 | IoT device connectivity |

---

## 3. Prerequisites

### 3.1 Hardware Requirements
- Raspberry Pi 3 or 4 (aarch64 architecture)
- Dual-interface capability (eth0 + wlan0)
- Minimum 2GB RAM
- MicroSD card with Debian Linux

### 3.2 Software Requirements
- Debian GNU/Linux 13 (Trixie) or compatible
- Root/sudo access
- Internet connectivity via eth0 (initial setup phase)

### 3.3 Pre-installation Steps Completed
✓ Raspberry Pi booted and accessible  
✓ Ethernet connection established (192.168.137.0/24)  
✓ Internet shared from laptop to Raspberry Pi

---

## 4. Installation & Configuration Procedure

### 4.1 Phase 1: System Update & Package Installation

#### 4.1.1 Update Package Manager & Install Dependencies

**Command:**
```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y hostapd dnsmasq iptables-persistent tcpdump ethtool
sudo systemctl unmask hostapd
sudo systemctl disable hostapd dnsmasq
```

**Purpose:**
- Update package repository indexes
- Upgrade system packages to latest versions
- Install required packages:
  - **hostapd**: WiFi Access Point daemon
  - **dnsmasq**: DHCP and DNS server
  - **iptables-persistent**: Persist firewall rules across reboots
  - **tcpdump**: Packet capture utility for traffic analysis
  - **ethtool**: Network interface diagnostics

**Outcome:** ✓ Completed successfully

---

### 4.2 Phase 2: Network Interface Configuration

#### 4.2.1 Detect Network Management System

**Initial Assessment:**
```
System uses: netplan + systemd-networkd
OS: Debian 13 (Trixie)
Network Manager: active
```

**Rationale:** Modern Debian distributions have migrated from dhcpcd to systemd-networkd. Configuration must use netplan format.

#### 4.2.2 Configure eth0 (Uplink Interface)

**Create netplan configuration:**
```bash
sudo cat > /etc/netplan/10-rpi-gateway.yaml << 'EOF'
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: false
      addresses:
        - 192.168.137.100/24
      routes:
        - to: 0.0.0.0/0
          via: 192.168.137.1
      nameservers:
        addresses: [8.8.8.8, 8.8.4.4]
EOF
```

**Configuration Details:**
- **IP Address:** 192.168.137.100/24 (same subnet as laptop)
- **Gateway:** 192.168.137.1 (laptop Ethernet interface)
- **DNS Servers:** Google DNS (8.8.8.8, 8.8.4.4)
- **DHCP:** Disabled (static configuration)

**Apply Configuration:**
```bash
sudo netplan apply
```

**Verification:**
```bash
ip addr show eth0
```

**Output:**
```
inet 192.168.137.100/24 brd 192.168.137.255 scope global noprefixroute eth0
```

✓ **Status:** eth0 correctly configured with static IP

#### 4.2.3 Configure wlan0 (Access Point Interface)

**Initial Assign IP Address (runtime test):**
```bash
sudo ip addr add 192.168.4.1/24 dev wlan0
```

**Make wlan0 Static IP Persistent Across Reboots (recommended):**
```bash
sudo tee /etc/systemd/system/wlan0-static-ip.service > /dev/null << 'EOF'
[Unit]
Description=Set static IP on wlan0 for IoT Gateway
Before=hostapd.service dnsmasq.service
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/sbin/ip link set wlan0 up
ExecStart=/sbin/ip addr replace 192.168.4.1/24 dev wlan0
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now wlan0-static-ip.service
```

**Verification:**
```bash
ip addr show wlan0
systemctl is-enabled wlan0-static-ip.service
systemctl is-active wlan0-static-ip.service
```

**Expected Output:**
```
inet 192.168.4.1/24 scope global wlan0
enabled
active
```

**Design Decision:** wlan0 uses subnet 192.168.4.0/24 (distinct from eth0) to create isolated IoT network segment.

✓ **Status:** wlan0 IP assigned and persisted

---

### 4.3 Phase 3: WiFi Access Point Configuration (hostapd)

#### 4.3.1 Create hostapd Configuration File

**File:** `/etc/hostapd/hostapd.conf`

```ini
interface=wlan0
driver=nl80211
ssid=IoT-Gateway
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_passphrase=12345678
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
country_code=US
```

#### 4.3.2 Configuration Parameter Explanation

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `interface` | wlan0 | Target wireless interface |
| `driver` | nl80211 | Linux wireless driver |
| `ssid` | IoT-Gateway | Network name broadcast |
| `hw_mode` | g | 802.11g (2.4 GHz) |
| `channel` | 6 | WiFi channel (1-13 in 2.4GHz) |
| `ieee80211n` | 1 | Enable 802.11n (HT) |
| `wmm_enabled` | 1 | WiFi Multimedia QoS |
| `wpa` | 2 | WPA2 security protocol |
| `wpa_passphrase` | 12345678 | WiFi password |
| `wpa_key_mgmt` | WPA-PSK | Pre-Shared Key authentication |
| `rsn_pairwise` | CCMP | AES cipher suite |
| `country_code` | US | Regulatory domain |

#### 4.3.3 Enable hostapd Service

**Verify/Update systemd service configuration:**
```bash
# /etc/default/hostapd should contain:
DAEMON_CONF="/etc/hostapd/hostapd.conf"
```

**Enable and Start Service:**
```bash
sudo systemctl enable hostapd
sudo systemctl restart hostapd
```

**Service Status:**
```
Active: active (running) since Tue 2026-04-07 17:21:49 +07
```

✓ **Status:** hostapd running - WiFi AP online

---

### 4.4 Phase 4: DHCP Server Configuration (dnsmasq)

#### 4.4.1 Backup & Create dnsmasq Configuration

**Backup original (for safety):**
```bash
sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak
```

**Create new `/etc/dnsmasq.conf`:**
```ini
interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
domain=local
bogus-priv
log-dhcp
```

#### 4.4.2 Configuration Parameter Details

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `interface` | wlan0 | DHCP on wireless interface |
| `dhcp-range` | 192.168.4.10-50 | IP pool for IoT devices |
| `domain` | local | Local domain suffix |
| `bogus-priv` | - | Prevent upstream queries for private ranges |
| `log-dhcp` | - | Log DHCP transactions (debugging) |

**Lease Duration:** 24h (86400 seconds)

#### 4.4.3 Enable dnsmasq Service

```bash
sudo systemctl enable dnsmasq
sudo systemctl restart dnsmasq
```

**Service Status:**
```
Active: active (running) since Tue 2026-04-07 17:21:49 +07
DHCP, IP range 192.168.4.10 -- 192.168.4.50, 1d
```

✓ **Status:** dnsmasq running - DHCP server operational

---

### 4.5 Phase 5: IP Forwarding Configuration

#### 4.5.1 Enable Kernel IP Forwarding

**Persistent File (recommended):** `/etc/sysctl.d/99-iot-gateway.conf`

```bash
sudo tee /etc/sysctl.d/99-iot-gateway.conf > /dev/null << 'EOF'
net.ipv4.ip_forward=1
EOF
```

**Apply Immediately:**
```bash
sudo sysctl --system
```

**Verification:**
```bash
cat /proc/sys/net/ipv4/ip_forward
# Output: 1
```

**Purpose:** Allow kernel to forward packets between interfaces (essential for gateway routing)

✓ **Status:** IP forwarding enabled and persisted

---

### 4.6 Phase 6: Network Address Translation (NAT) & Firewall

#### 4.6.1 Configure iptables Rules

**NAT Rule - Allow IoT devices to reach upstream:**
```bash
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
```

**Purpose:** Masquerade outgoing traffic from IoT network (wlan0) as originating from gateway's eth0 IP

**Forward Rules - Enable bidirectional traffic:**
```bash
sudo iptables -A FORWARD -i eth0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT
```

**Purpose:** Allow established connections from upstream to IoT devices

```bash
sudo iptables -A FORWARD -i wlan0 -o eth0 -j ACCEPT
```

**Purpose:** Allow IoT devices to initiate connections to upstream

#### 4.6.2 Persist Rules Across Reboots

**Save current rules:**
```bash
sudo netfilter-persistent save
```

**Output:**
```
running /usr/share/netfilter-persistent/plugins.d/15-ip4tables save
running /usr/share/netfilter-persistent/plugins.d/25-ip6tables save
```

**Verification - Persistent rules are stored in:**
```
/etc/iptables/rules.v4
/etc/iptables/rules.v6
```

✓ **Status:** NAT rules configured and persistent

---

### 4.7 Phase 7: Service Initialization

#### 4.7.1 Enable Services for Auto-start

```bash
sudo systemctl enable hostapd dnsmasq
```

#### 4.7.2 Start/Restart Services

```bash
sudo systemctl restart hostapd dnsmasq
```

#### 4.7.3 Verify Service Status

**hostapd Status:**
```
Unit: hostapd.service
Status: active (running)
Process: /usr/sbin/hostapd -B -P /run/hostapd.pid /etc/hostapd/hostapd.conf
Main PID: 6531
```

**dnsmasq Status:**
```
Unit: dnsmasq.service
Status: active (running)
Process: /usr/share/dnsmasq/systemd-helper exec
Main PID: 6548
```

✓ **Status:** All services running successfully

---

### 4.8 Phase 8: Reboot Persistence Validation

After initial setup, a reboot test is required to confirm autonomous recovery.

**Observed Before Permanent Fix:**
- `hostapd` and `dnsmasq` were active
- SSID `IoT-Gateway` was visible
- `wlan0` IPv4 `192.168.4.1/24` was missing
- `ip_forward` returned `0`

**Permanent Fix Applied:**
- Added `wlan0-static-ip.service` to restore wlan0 IP at boot
- Added `/etc/sysctl.d/99-iot-gateway.conf` to force `net.ipv4.ip_forward=1`

**Post-fix Verification Commands:**
```bash
systemctl is-enabled wlan0-static-ip hostapd dnsmasq
systemctl is-active wlan0-static-ip hostapd dnsmasq
ip -4 addr show wlan0
cat /proc/sys/net/ipv4/ip_forward
```

**Post-fix Result:**
- All three services enabled and active
- `wlan0` has `192.168.4.1/24`
- `ip_forward = 1`

✓ **Status:** Gateway startup is fully automatic after reboot

---

## 5. Verification & Testing

### 5.1 Network Configuration Verification

#### 5.1.1 Check Interface Status

**eth0 (Uplink):**
```bash
ip addr show eth0
```

**Output:**
```
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...
    inet 192.168.137.100/24 brd 192.168.137.255 scope global eth0
```

- Status: UP/LOWER_UP ✓
- IP: 192.168.137.100/24 ✓
- MTU: 1500 bytes (standard) ✓

**wlan0 (IoT AP):**
```bash
ip addr show wlan0
```

**Output:**
```
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...
    inet 192.168.4.1/24 scope global wlan0
```

- Status: UP/LOWER_UP ✓
- IP: 192.168.4.1/24 ✓
- MTU: 1500 bytes ✓

#### 5.1.2 Routing Table Verification

```bash
ip route
```

**Output:**
```
default via 192.168.137.1 dev eth0 proto static metric 100
192.168.4.0/24 dev wlan0 proto kernel scope link src 192.168.4.1
192.168.137.0/24 dev eth0 proto kernel scope link src 192.168.137.100
```

**Analysis:**
- Default route via laptop (192.168.137.1) ✓
- Direct route to 192.168.4.0/24 via wlan0 ✓
- Direct route to 192.168.137.0/24 via eth0 ✓

### 5.2 WiFi Access Point Verification

#### 5.2.1 WiFi Interface Status

```bash
sudo iw dev wlan0 info
```

**Output:**
```
Interface wlan0
    ifindex 3
    wdev 0x1
    addr 88:a2:9e:72:0a:dc
    ssid IoT-Gateway
    type AP
    wiphy 0
    channel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz
    txpower 31.00 dBm
```

**Configuration Status:**
- SSID Broadcasting: IoT-Gateway ✓
- WiFi Type: Access Point ✓
- Channel: 6 (2437 MHz) ✓
- Transmit Power: 31.00 dBm (maximum) ✓

### 5.3 DHCP Server Verification

```bash
sudo systemctl status dnsmasq
```

**Key Indicators:**
```
DHCP, IP range 192.168.4.10 -- 192.168.4.50, 1d
using nameserver 8.8.8.8#53
using nameserver 8.8.4.4#53
```

- DHCP Server: Active ✓
- IP Range: 192.168.4.10-50 ✓
- Lease Duration: 24 hours ✓
- DNS Servers: Configured ✓

### 5.4 Packet Forwarding Verification

```bash
cat /proc/sys/net/ipv4/ip_forward
```

**Output:** `1` ✓

```bash
sudo iptables -L FORWARD -nv
```

**Expected Rules Present:**
- Allow RELATED,ESTABLISHED from eth0 to wlan0 ✓
- Allow NEW from wlan0 to eth0 ✓

```bash
sudo iptables -t nat -L POSTROUTING -nv
```

**Expected Rules Present:**
- MASQUERADE on eth0 for wlan0 traffic ✓

---

## 6. Gateway Operational Summary

### 6.1 Configuration Checklist

| Component | Configuration | Status |
|-----------|---------------|--------|
| **eth0 Interface** | Static 192.168.137.100/24 | ✓ Active |
| **wlan0 Interface** | Static 192.168.4.1/24 | ✓ Active |
| **WiFi SSID** | IoT-Gateway | ✓ Broadcasting |
| **WiFi Security** | WPA2-PSK AES | ✓ Enabled |
| **WiFi Password** | 12345678 | ✓ Configured |
| **DHCP Server** | Range 192.168.4.10-50 | ✓ Active |
| **IP Forwarding** | Enabled (=1) | ✓ Active |
| **NAT Rules** | eth0↔wlan0 Masquerade | ✓ Persistent |
| **wlan0-static-ip Service** | Enabled, auto-start | ✓ Running |
| **hostapd Service** | Enabled, auto-start | ✓ Running |
| **dnsmasq Service** | Enabled, auto-start | ✓ Running |

### 6.2 Gateway Readiness Status

**[✓] GATEWAY FULLY OPERATIONAL**

---

## 7. Testing Procedure for IoT Devices

### 7.1 Device Connection Test

**On IoT Device (ESP32/NodeMCU):**

1. **Scan WiFi Networks:**
   - Look for SSID: `IoT-Gateway`

2. **Connect to WiFi:**
   - SSID: IoT-Gateway
   - Password: 12345678
   - Security: WPA2

3. **Verify IP Assignment:**
   - Expected IP range: 192.168.4.10 - 192.168.4.50
   - Gateway: 192.168.4.1
   - DNS: 192.168.4.1

4. **Connectivity Test:**
   ```
   ping 192.168.4.1        (ping gateway)
   ping 192.168.137.1      (ping upstream)
   ```

### 7.2 Gateway-side Traffic Monitoring

**Capture all traffic on wlan0:**
```bash
sudo tcpdump -i wlan0 -nn
```

**Sample output when device connects:**
```
dhcp packets: DISCOVER, OFFER, REQUEST, ACK
probe requests: device scanning WiFi
association frames: device joining network
```

**Capture to PCAP file (for IDS training):**
```bash
sudo tcpdump -i wlan0 -w iot_traffic.pcap
```

**Stop with:** Ctrl+C

---

## 8. Next Phase: IDS Implementation

### 8.1 Baseline Data Collection
- Capture normal traffic from connected devices
- Document MQTT/CoAP communication patterns
- Record packet-level features:
  - Source/Destination IPs
  - Source/Destination Ports
  - Protocol types (TCP/UDP)
  - Packet sizes and inter-arrival times
  - Flags and packet counts per flow

### 8.2 Attack Scenario Simulation
- Port scanning traffic collection
- DDoS (Denial of Service) traffic simulation
- Anomalous communication patterns
- Protocol violation attempts

#### 8.2.1 Port Scanning Test (Authorized Lab Only)

Use a separate attacker machine in the same lab network to generate controlled scan traffic toward the gateway.

Prerequisites:
- Attacker node can reach gateway IP (example: 192.168.4.1)
- IDS capture is running on gateway wlan0
- Test only in isolated lab environment

On attacker machine:

```bash
# Fast SYN scan on common ports
nmap -sS -T4 -p 1-1024 192.168.4.1

# Full TCP port range scan (heavier)
nmap -sS -T4 -p- 192.168.4.1

# Service/version detection on discovered ports
nmap -sV 192.168.4.1
```

On Raspberry Pi gateway (monitor during scan):

```bash
# Packet-level confirmation of scan behavior
sudo tcpdump -i wlan0 -nn 'host 192.168.4.1 and tcp'

# Optional: save scan session to PCAP for feature extraction/training
sudo tcpdump -i wlan0 -w portscan_session.pcap 'host 192.168.4.1 and tcp'
```

Expected observations:
- High number of SYN packets over many destination ports
- Elevated unique destination port count in short time window
- IDS should score this pattern as suspicious compared to normal IoT telemetry

#### 8.2.2 DDoS Simulation Scripts (Authorized Lab Only)

Reusable scripts are provided in:
- `scripts/attack_sim/simulate_ddos.py`
- `scripts/attack_sim/run_ddos_profiles.ps1`

Run from attacker machine (Windows PowerShell):

```powershell
# Medium mixed profile against gateway MQTT port
.\scripts\attack_sim\run_ddos_profiles.ps1 -TargetIp 192.168.4.1 -Mode mixed -Profile medium -Ports 1883

# UDP-heavy profile
.\scripts\attack_sim\run_ddos_profiles.ps1 -TargetIp 192.168.4.1 -Mode udp -Profile high -Ports 1883

# TCP connect flood profile
.\scripts\attack_sim\run_ddos_profiles.ps1 -TargetIp 192.168.4.1 -Mode tcp -Profile medium -Ports 1883
```

Run directly with Python (Windows/Linux/macOS):

```bash
python scripts/attack_sim/simulate_ddos.py \
  --target-ip 192.168.4.1 \
  --ports 1883 \
  --mode mixed \
  --duration 30 \
  --workers 8 \
  --rate 200 \
  --payload-size 128 \
  --i-understand-lab-only
```

On Raspberry Pi gateway during simulation:

```bash
# Observe attack traffic at packet level
sudo tcpdump -i wlan0 -nn 'host 192.168.4.1 and (tcp or udp)'

# Observe IDS decisions
python -m ids_gateway.main
```

Expected behavior:
- Attack node generates bursty high-rate traffic
- IDS score for attacker source IP increases
- Gateway may block attacker IP when threshold is exceeded

### 8.3 Machine Learning Model Preparation
- Data preprocessing and feature engineering
- Model selection (Random Forest, LightGBM)
- Training on laptop with training dataset
- Model export and optimization for Raspberry Pi
- On-device inference implementation

---

## 9. Troubleshooting Reference

### 9.1 Common Issues & Solutions

| Issue | Symptom | Solution |
|-------|---------|----------|
| WiFi not broadcasting | SSID not visible | Check hostapd service: `sudo systemctl restart hostapd` |
| DHCP not working | Device can't get IP | Verify dnsmasq: `sudo systemctl restart dnsmasq` |
| No internet on device | Device connected but can't reach upstream | Check NAT rules: `sudo iptables -t nat -L POSTROUTING` |
| SSID visible but routing fails after reboot | Device connects but no gateway reachability | Verify `wlan0-static-ip` and `ip_forward`: `systemctl is-active wlan0-static-ip && cat /proc/sys/net/ipv4/ip_forward` |
| eth0 has no IP | Can't reach laptop | Check netplan: `sudo netplan apply` |
| wlan0 shows DOWN | AP unavailable | Bring up interface: `sudo ip link set wlan0 up` |

### 9.2 Service Status Check

```bash
# Check all relevant services
sudo systemctl status hostapd dnsmasq

# View recent logs
sudo journalctl -u hostapd -n 20
sudo journalctl -u dnsmasq -n 20

# Monitor real-time activity
sudo tcpdump -i wlan0 -e -tttt
```

---

## 10. Configuration Files Summary

### 10.1 Modified/Created Files

| File | Purpose | Location |
|------|---------|----------|
| `10-rpi-gateway.yaml` | Network interface config | `/etc/netplan/` |
| `hostapd.conf` | WiFi AP settings | `/etc/hostapd/` |
| `dnsmasq.conf` | DHCP/DNS server config | `/etc/` |
| `99-iot-gateway.conf` | Persistent kernel IP forward setting | `/etc/sysctl.d/` |
| `wlan0-static-ip.service` | Restore wlan0 static IP at boot | `/etc/systemd/system/` |
| `rules.v4` | IPv4 firewall rules (persistent) | `/etc/iptables/` |

### 10.2 Backup Files

```bash
/etc/dnsmasq.conf.bak    # Original dnsmasq config
```

---

## 11. System Specifications

### 11.1 Hardware
- **Model:** Raspberry Pi (ARM aarch64)
- **Kernel:** Linux 6.12.75-rpt-rpi-v8
- **Architecture:** 64-bit (ARMv8)

### 11.2 Operating System
- **OS:** Debian GNU/Linux 13 (Trixie)
- **Package Manager:** apt (Debian)
- **Init System:** systemd

### 11.3 Network Services

| Service | Version | Role |
|---------|---------|------|
| hostapd | (system) | WiFi Access Point |
| dnsmasq | 2.91 | DHCP + DNS Server |
| iptables | (kernel) | Firewall/NAT |
| netplan | (system) | Network management |

---

## 12. Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.1 | 2026-04-07 | Engineering Team | Added reboot persistence fix (`wlan0-static-ip.service`, `/etc/sysctl.d/99-iot-gateway.conf`), corrected topology IP text, and added reboot validation results |
| 1.0 | 2026-04-07 | Engineering Team | Initial setup and configuration |

---

## 13. Appendix: Command Reference

### Quick Gateway Status Check
```bash
# Network status
echo "=== Interface IPs ===" && ip addr show eth0 && ip addr show wlan0
echo "=== Routes ===" && ip route
echo "=== Service Status ===" && sudo systemctl status wlan0-static-ip hostapd dnsmasq --no-pager
echo "=== WiFi Info ===" && sudo iw dev wlan0 info
echo "=== IP Forward ===" && cat /proc/sys/net/ipv4/ip_forward
```

### Enable/Disable Gateway Services
```bash
# Stop gateway
sudo systemctl stop hostapd dnsmasq

# Start gateway
sudo systemctl start hostapd dnsmasq

# Restart gateway
sudo systemctl restart hostapd dnsmasq
```

### Restart Network Configuration
```bash
sudo netplan apply
sudo systemctl restart wlan0-static-ip hostapd dnsmasq
sudo sysctl --system
```

---

**Document Classification:** Technical Documentation  
**Audience:** Network Engineers, IoT Security Specialists  
**Last Updated:** April 7, 2026
