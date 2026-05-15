# Workflow vận hành mô hình IDS trên Gateway Edge

Phiên bản: 1.0
Ngày: 2026-05-14

Mục đích: Mô tả cách thực thi mô hình trên Raspberry Pi gateway, luồng dữ liệu, định dạng output khi phát hiện tấn công và logic/nguyên tắc ngăn chặn.

## 1. Tổng quan luồng vận hành
1. Capture: Gateway (Pi) bắt các gói trên `wlan0` bằng module `capture.py` theo cửa sổ thời gian cấu hình (`packet_window_seconds` trong `configs/ids_config.yaml`).
2. Aggregate & Feature extraction: Các gói trong cửa sổ được gom thành `PacketRecord` và chuyển qua `features.build_feature_vector()` để tạo vector 10 feature (hoặc mở rộng nếu config yêu cầu).
3. Preprocess: Vector feature được chuẩn hóa bằng `models/feature_scaler.joblib` (StandardScaler) trước khi đưa vào mô hình.
4. Stage 1 - Binary detection:
   - Chạy mô hình nhị phân (`model_stage1` — RandomForest/LightGBM) để xác định `is_attack` dựa trên xác suất tấn công và `stage1_threshold`.
   - Kết quả: score (0..1), `is_attack` (True/False).
5. Stage 2 - Family classification (nếu `is_attack` True):
   - Chạy mô hình đa lớp (`model_stage2`) để ước đoán `attack_family` (ví dụ: ddos, portscan, brute_force, botnet, web_attack, recon, attack_other).
   - Kết quả: family label + family_prob.
6. Decision logic: Dựa trên family, score, số lần liên tiếp phát hiện trong các cửa sổ liên tiếp và whitelist/blacklist, hệ thống quyết định hành động (alert, throttle, block).
7. Action & output: Khi phát hiện attack theo chính sách, gateway thực thi hành động chặn và ghi log/emit alert.

## 2. Chi tiết thực thi (running environment)
- Nơi chạy: Raspberry Pi (Raspbian/Ubuntu), service systemd `ids_gateway.service` chạy `python -m ids_gateway.main`.
- Tần suất inference: cấu hình `inference_interval_seconds` (mặc định 2s) — mô hình thực thi trên mỗi cửa sổ packet (window).
- Tài nguyên: model và scaler load 1 lần khi service khởi động; inference dùng CPU, tối ưu cho Pi (RandomForest/LightGBM nhẹ).

## 3. Định dạng output khi phát hiện
Khi một sự kiện tấn công được xác nhận, gateway sẽ tạo một event (JSON) và lưu/emit như sau:

- Trường bắt buộc trong event JSON:
  - `timestamp`: ISO 8601
  - `source_ip`: IP nguồn được block/được phát hiện
  - `dst_ip`: đích (nếu có)
  - `attack_family`: ddos|portscan|brute_force|botnet|web_attack|recon|attack_other
  - `family_prob`: xác suất do model_stage2 trả về
  - `attack_score`: xác suất stage1
  - `window_start` / `window_end`: phạm vi thời gian của cửa sổ
  - `action`: alert|throttle|block|quarantine
  - `evidence`: ngắn gọn các feature chính dẫn đến quyết định (ví dụ pkt_count, unique_dst_port_count)

- Nơi lưu/emit:
  - Local log file (rotate) và gửi tới Promtail/Loki nếu cấu hình có sẵn
  - Tùy chọn: gửi HTTP webhook tới Nodered/ops, hoặc publish MQTT lên topic `ids/events`

## 4. Logic ngăn chặn tấn công (prevention policy)
Mục tiêu: phản ứng nhanh đủ để giảm tác hại, đồng thời tránh block nhầm thiết bị hợp lệ.

### 4.1 Nguyên tắc chung
- Luôn kiểm tra danh sách whitelist trước khi chặn (trusted IP/MAC không bị block tự động).
- Dùng cơ chế bằng chứng lặp (consecutive-windows): yêu cầu N cửa sổ liên tiếp có `is_attack` để quyết định block, với ngưỡng tối thiểu cho mỗi family.
- Có cơ chế soft-block (throttle) trước khi block hoàn toàn nếu tác động có thể gây gián đoạn dịch vụ IoT.
- Ghi lại và thông báo cho operator khi thực hiện block.

### 4.2 Hành động theo attack_family (chính sách mẫu)
- ddos:
  - Khi `attack_score` >= `stage1_threshold` và family_prob cao: **block** nguồn bằng iptables DROP hoặc thêm vào `ipset` + rule nhanh.
  - Thời gian block mặc định `default_block_seconds` (configurable); có thể tăng nếu attack lặp lại.
- portscan / recon:
  - Áp dụng **throttle** (rate-limit) + temporary block (short ban, ví dụ 60s) bằng nftables/iptables hoặc `conntrack --remove` để xóa kết nối tạm thời.
- brute_force (ví dụ SSH brute):
  - Giới hạn kết nối (fail2ban-like): tăng delay, temporary block và gửi alert chi tiết cho operator.
- botnet:
  - Block + quarantine (move to VLAN/isolated subnet) nếu hạ tầng hỗ trợ; luôn gửi ghi chú forensic.
- web_attack (HTTP exploit):
  - Nếu có WAF tích hợp (mod_security/nginx rules) -> apply app-level block; nếu không, throttle và alert.

### 4.3 Cơ chế thực thi blocking (kỹ thuật)
- Cách thực hiện kỹ thuật:
  - ipset + iptables: thêm nhanh hàng loạt IP vào ipset, iptables tham chiếu ipset để DROP (ít tốn CPU hơn so với nhiều rule iptables).
  - nftables (nếu dùng): tương tự ipset logic.
  - tc (traffic control) hoặc iptables rate-limit cho throttle.
  - conntrack removal để dọn session hiện tại.
- Rollback/expiration: các block được ghi kèm `expiry` (timestamp); một worker kiểm tra và xoá rule khi hết hạn.

### 4.4 Kiểm tra an toàn trước khi block
- Kiểm tra không chặn gateway / dhcp / dns server nội bộ.
- Nếu số lượng nguồn bị block vượt mức (ngưỡng hệ thống), kích hoạt chế độ cảnh báo và tắt auto-block để tránh false-positive lan rộng.

## 5. Thuật toán và mô hình sử dụng cho detection
- Stage 1 (binary detector): RandomForest hoặc LightGBM (gắn với pipeline hiện tại). Mục tiêu: recall cao, latency thấp.
- Stage 2 (family classifier): LightGBM multiclass (ưu tiên) hoặc RandomForest multi-class.
- Lý do: tree-based models dễ huấn luyện, robust với feature scale, inference nhanh trên CPU Pi.

## 6. Quy trình ghi nhận và audit
- Mọi action block/throttle phải kèm log ghi chi tiết (timestamp, evidence, model_scores, operator_override flag).
- Lưu các mẫu PCAP khi xảy ra incident (tuỳ chọn, yêu cầu lưu trữ) để phân tích forensics.

## 7. Cấu hình khuyến nghị (ví dụ trong `configs/ids_config.yaml`)
```yaml
gateway:
  default_block_seconds: 600
  packet_window_seconds: 10
  inference_interval_seconds: 2
  stage1_threshold: 0.7
  stage1_consecutive_windows: 2
  stage2_confidence_threshold: 0.6
  whitelist_ips: []

model:
  stage1_path: models/model_stage1.joblib
  stage2_path: models/model_stage2.joblib
  scaler_path: models/feature_scaler.joblib
```

## 8. Ghi chú vận hành
- Trước khi đưa vào production, chạy test end-to-end trong lab (ESP32 normal + attacker node) và kiểm chứng thời gian phát hiện + false-positive rate.
- Có cơ chế manual-unblock và audit trail.

---

File này có thể mở rộng thêm playbook chi tiết (ví dụ: lệnh ipset/iptables, ví dụ log JSON, ví dụ webhook payload). Nêu bạn muốn mình bổ sung phần đó, mình sẽ tiếp tục viết.