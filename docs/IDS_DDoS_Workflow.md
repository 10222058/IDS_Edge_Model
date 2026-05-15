# Workflow mô hình phát hiện DDoS trên Gateway Edge

Phiên bản: 1.0
Ngày: 2026-05-14

Mục đích: mô tả chi tiết cách thực thi mô hình DDoS (từ capture → inference → hành động), định dạng output khi phát hiện và cách chặn DDoS trên Raspberry Pi.

## 1. Tóm tắt luồng
1. Capture gói trên `wlan0` theo cửa sổ `packet_window_seconds` (ví dụ 10s).
2. Tạo feature vector từ các gói (dùng `features.build_feature_vector()` → 10 feature mặc định).
3. Chuẩn hóa bằng `models/feature_scaler.joblib`.
4. Chạy mô hình DDoS binary (`models/ids_model.joblib` hoặc `models/model_stage1.joblib`) để nhận `attack_score`.
5. Nếu `attack_score >= stage1_threshold` và/hoặc xuất hiện trong N cửa sổ liên tiếp, hệ thống quyết định hành động: `alert` → `throttle` → `block`.
6. Ghi event JSON và thực thi chặn (ipset + iptables) theo chính sách.

## 2. Nơi chạy và tần suất
- Chạy trên Raspberry Pi dưới service systemd `ids_gateway.service`.
- Mỗi `inference_interval_seconds` (mặc định 2s) thực hiện lấy window mới và inference.
- Model và scaler load vào bộ nhớ khi service khởi động.

## 3. Điều kiện phát hiện (example)
- `stage1_threshold` mặc định: 0.7 (tối ưu bằng threshold sweep).
- Yêu cầu `consecutive_windows`: 2 (phát hiện trong 2 cửa sổ liên tiếp mới hành động block).
- Có thể kết hợp `min_pkt_count` để tránh block nhầm khi mẫu quá nhỏ.

## 4. Output khi phát hiện
Khi quyết định hành động, tạo event JSON như sau:

```json
{
  "timestamp": "2026-05-14T12:34:56Z",
  "source_ip": "192.168.4.101",
  "attack_score": 0.93,
  "window_start": "2026-05-14T12:34:50Z",
  "window_end": "2026-05-14T12:35:00Z",
  "action": "block",
  "reason": "ddos_high_pkt_rate",
  "evidence": { "pkt_count": 1200, "udp_count": 1200, "mean_inter_arrival_ms": 0.5 }
}
```

- Event được ghi vào log local và publish tới MQTT topic `ids/events` hoặc gửi webhook tới Nodered/ops.

## 5. Logic ngăn chặn DDoS (chi tiết)
### 5.1 Nguyên tắc
- Kiểm whitelist trước khi block.
- Dùng soft-step: `alert` → `throttle` (rate-limit) → `block` (DROP) nếu tấn công tiếp tục.
- Block theo IP nguồn; dùng `ipset` để quản lý nhóm IP nhanh.
- Block có expiry (ví dụ `default_block_seconds` = 600s) và worker tự làm sạch các mục đã hết hạn.

### 5.2 Chính sách mẫu
- Nếu `attack_score >= 0.95` ngay lập tức `block` (tùy cấu hình).
- Nếu `0.75 <= attack_score < 0.95` và xuất hiện trong 2 cửa sổ liên tiếp → `block`.
- Nếu `attack_score >= 0.6` → `throttle` (ví dụ giới hạn pps cho IP nguồn).

## 6. Lệnh thực thi (playbook ví dụ)
### 6.1 Tạo ipset và iptables rule (một lần)
```bash
# Tạo ipset:
sudo ipset create ids_block hash:ip maxelem 65536

# Thêm chain iptables tham chiếu ipset (IPv4)
sudo iptables -N IDS_BLOCK || true
sudo iptables -I INPUT -m set --match-set ids_block src -j DROP
```

### 6.2 Block 1 IP nhanh
```bash
sudo ipset add ids_block 192.168.4.101
# optional: log
sudo logger "IDS blocked 192.168.4.101"
```

### 6.3 Throttle IP (tạm thời giới hạn kết nối)
```bash
# ví dụ dùng tc để giới hạn băng thông (root required)
sudo tc qdisc add dev wlan0 root handle 1: htb default 12
# ... (thiết lập class và filter per-IP) — tuỳ môi trường
```

### 6.4 Xóa IP khỏi ipset (rollback khi hết hạn)
```bash
sudo ipset del ids_block 192.168.4.101
```

## 7. Safety checks trước khi block
- Không block các IP trong `whitelist_ips` (gateway, DHCP server, NMS).
- Nếu số IP bị block vượt `max_auto_block` (ví dụ 100), dừng auto-block và alert operator.

## 8. Testing & validation
1. Chuẩn bị: khởi chạy `ids_gateway` service, monitor logs.
2. Baseline: chạy ESP32 mock traffic 10 phút → đảm bảo không có alert/block.
3. DDoS test: chạy `scripts/attack_sim/simulate_ddos.py --mode udp --rate 200 --duration 60 --i-understand-lab-only` từ attacker node.
4. Quan sát: thời gian phát hiện (goal < 10s), event JSON xuất ra, IP nguồn được thêm vào `ids_block`.

## 9. Cấu hình khuyến nghị (ví dụ)
```yaml
model:
  path: models/ids_model.joblib
  scaler_path: models/feature_scaler.joblib

gateway:
  packet_window_seconds: 10
  inference_interval_seconds: 2
  stage1_threshold: 0.7
  stage1_consecutive_windows: 2
  default_block_seconds: 600
  whitelist_ips: ["192.168.4.1"]
```

## 10. Ghi chú vận hành
- Luôn test trong lab trước khi enable auto-block trên mạng thực tế.
- Cân nhắc chế độ monitor-only (alert nhưng không block) trong tuần đầu triển khai.

---

Muốn mình thêm: (A) playbook iptables/nftables đầy đủ (bao gồm systemd-timer cleanup), hoặc (B) ví dụ webhook payload + Nodered flow để nhận alert?