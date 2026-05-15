# Tài liệu triển khai & vận hành mô hình IDS (Toàn bộ)

Phiên bản: 1.0
Ngày: 2026-05-15

Mục đích: hướng dẫn đầy đủ cách cài đặt, chạy, tích hợp mô hình IDS (hiện ưu tiên DDoS) vào dự án, mô tả file cần thiết, logic quyết định và giải thích thuật toán.

---

## 1. Yêu cầu & chuẩn bị môi trường
- OS: Raspberry Pi OS / Ubuntu 20.04+ cho gateway. Máy huấn luyện có thể là laptop/VM có Python 3.10+.
- Cài dependencies (từ workspace):

```bash
python -m pip install -r requirements.txt
```

- Yêu cầu mạng: `wlan0` (AP) capture, broker MQTT (mosquitto) lắng nghe 0.0.0.0:1883.

## 2. File quan trọng trong repo và vai trò
- `train/prepare_ids_dataset.py` — hợp nhất và chuẩn hóa dataset, mapping `attack_family`.
- `train/train_ids_model.py` — script huấn luyện model (stage1 binary, stage2 multiclass tùy mở rộng).
- `train/evaluate_ids_model.py` — đánh giá, sweep threshold, xuất `reports/`.
- `ids_gateway/`:
  - `capture.py` — bắt packet, tạo `PacketRecord`.
  - `features.py` — chuyển packet list → feature vector (10 feature mặc định).
  - `main.py` — runtime service load model, vòng inference, action (đường chính).
  - `detector.py` / `model.py` — helper load model / predict (nếu có).
- `configs/ids_config.yaml` — cấu hình runtime (thresholds, windows, block seconds, whitelist, model paths).
- `models/` — nơi lưu artifact: `ids_model.joblib`, `feature_scaler.joblib`, (mở rộng: `model_stage1.joblib`, `model_stage2.joblib`).
- `scripts/attack_sim/simulate_ddos.py` — mô phỏng DDoS để test.
- `docs/diagram.mmd` — sơ đồ workflow (Mermaid).

## 3. Cài đặt ban đầu
1. Cài Python dependencies như ở mục 1.
2. Chuẩn bị dữ liệu (local):
```bash
python train/prepare_ids_dataset.py --input data/raw --output data/processed/ids_features.csv
```
3. Huấn luyện model (RandomForest mặc định):
```bash
cd train
python train_ids_model.py
```
Kết quả: `models/ids_model.joblib`, `models/feature_scaler.joblib`.
4. Chạy đánh giá và điều chỉnh threshold:
```bash
python evaluate_ids_model.py --config ../configs/ids_config.yaml --report-dir ../reports
```
Xem `reports/threshold_sweep.csv` để chọn `stage1_threshold` phù hợp.

## 4. Triển khai lên Gateway (Pi)
1. Copy artifacts:
- `models/ids_model.joblib` → `/home/pi/project/models/`
- `models/feature_scaler.joblib` → `/home/pi/project/models/`
- `configs/ids_config.yaml` → `/home/pi/project/configs/`
2. Tạo systemd unit (ví dụ `/etc/systemd/system/ids_gateway.service`):

```ini
[Unit]
Description=IDS Gateway Service
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/project
ExecStart=/usr/bin/python3 -m ids_gateway.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

3. Enable & start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ids_gateway.service
sudo journalctl -u ids_gateway.service -f
```

## 5. Cách chương trình chạy (execution flow)
1. `ids_gateway.main` load `feature_scaler` và `model` khi khởi động.
2. Tham số cấu hình chính (`configs/ids_config.yaml`):
  - `packet_window_seconds`: cửa sổ gom packet (ví dụ 10s)
  - `inference_interval_seconds`: khoảng thời gian chạy inference (ví dụ 2s)
  - `stage1_threshold`, `stage1_consecutive_windows`, `default_block_seconds`, `whitelist_ips`.
3. Vòng chính (per-window):
  - Bắt packets trong window → build vector bằng `features.build_feature_vector()`
  - Nếu vector đủ điều kiện (`pkt_count >= MIN_PKTS`) → scale bằng `feature_scaler`
  - `model_prob = model.predict_proba(x)[0,1]` (stage1)
  - `stat_score = compute_stat_score(features)` (heuristic)
  - `attack_score = w_model*model_prob + w_stat*stat_score`
  - Nếu `attack_score` vượt threshold theo policy (consecutive windows, whitelist check) → hành động (throttle/block), emit event JSON.

  ## 6. Giải thích input của model và cách tính toán

  ### 6.1 Model nhận input như thế nào
  - Model không nhận packet thô trực tiếp, mà nhận **feature vector theo từng cửa sổ thời gian**.
  - Mỗi cửa sổ (ví dụ 10 giây) gồm nhiều gói tin từ cùng nguồn hoặc cùng phiên gom nhóm.
  - Từ danh sách packet đó, hệ thống tạo ra 1 dòng dữ liệu gồm các cột số để inference.

  Pipeline input:
  1. `capture.py` bắt gói và tạo `PacketRecord`.
  2. `features.py` gom packet theo window.
  3. `build_feature_vector()` xuất ra dict 10 feature.
  4. Dict feature được map đúng thứ tự `expected_features` trong config.
  5. `feature_scaler.joblib` chuẩn hóa trước khi vào model.

  ### 6.2 Kiểu dữ liệu và schema input
  Schema chuẩn (mỗi hàng là 1 mẫu):

  | Feature | Kiểu dữ liệu | Ý nghĩa |
  |---|---|---|
  | `pkt_count` | float | Tổng số packet trong cửa sổ |
  | `tcp_count` | float | Số packet TCP |
  | `udp_count` | float | Số packet UDP |
  | `icmp_count` | float | Số packet ICMP |
  | `syn_count` | float | Số cờ SYN |
  | `ack_count` | float | Số cờ ACK |
  | `rst_count` | float | Số cờ RST |
  | `unique_dst_port_count` | float | Số cổng đích khác nhau |
  | `avg_pkt_len` | float | Độ dài gói trung bình |
  | `mean_inter_arrival_ms` | float | Khoảng cách thời gian trung bình giữa các gói (ms) |

  Nhãn huấn luyện:
  - `label`: int (0 = normal, 1 = attack)
  - `attack_family`: string (ví dụ `ddos`, `portscan`, `normal`) để breakdown hoặc stage 2.

  ### 6.3 Phân loại dữ liệu trong pipeline
  - **Runtime (online inference):** nhận packet stream trực tiếp từ mạng gateway.
  - **Training/Evaluation (offline):** nhận dataset tabular (`csv/parquet`) đã chuẩn hóa về schema trên.
  - **Theo lớp:**
    - Normal traffic (`label = 0`)
    - Attack traffic (`label = 1`)
  - **Theo family (mở rộng):** `ddos`, `portscan`, `recon`, `attack_other`...

  ### 6.4 Tính toán feature từ packet
  - `pkt_count`: đếm số bản ghi packet trong window.
  - `tcp_count`, `udp_count`, `icmp_count`: đếm theo protocol.
  - `syn_count`, `ack_count`, `rst_count`: đếm ký tự cờ TCP trong toàn window.
  - `unique_dst_port_count`: cardinality tập cổng đích > 0.
  - `avg_pkt_len`: trung bình `pkt_len`.
  - `mean_inter_arrival_ms`: trung bình chênh lệch thời gian giữa 2 packet liên tiếp.

  ### 6.5 Từ input đến `attack_score`
  Với mỗi mẫu input sau khi scale:
  1. Model trả về xác suất tấn công:
    - `model_prob = model.predict_proba(x)[0, 1]`
  2. Heuristic engine tính điểm thống kê:
    - `stat_score` dựa trên burst-rate/SYN-rate/entropy (chuẩn hóa về [0,1]).
  3. Kết hợp để có `attack_score`:
    - `attack_score = w_model * model_prob + w_stat * stat_score`
    - Ví dụ: `w_model = 0.8`, `w_stat = 0.2`.

  Ý nghĩa:
  - `model_prob` phản ánh học từ dữ liệu lịch sử.
  - `stat_score` phản ánh hành vi tức thời bất thường (đột biến lưu lượng).
  - `attack_score` là điểm cuối cùng dùng cho policy quyết định block/throttle.

  ### 6.6 Ví dụ tính nhanh
  Giả sử 1 window có:
  - `pkt_count=1200`, `udp_count=1200`, `mean_inter_arrival_ms=0.5`
  - Model dự đoán `model_prob=0.91`
  - Heuristic cho `stat_score=0.80`

  Khi đó:
  - `attack_score = 0.8*0.91 + 0.2*0.80 = 0.888`

  Nếu `LOW_THR=0.7` và thỏa điều kiện `consecutive_windows`, mẫu này sẽ bị đánh dấu attack để chuyển sang bước hành động.

### 6.7 Chế độ "theo dõi thêm" (monitoring / escalation) — giải thích cho người mới

Khi một mẫu rơi vào vùng "theo dõi" (ví dụ `LOW_THR <= attack_score < HIGH_THR`), hệ thống không block ngay lập tức mà bắt đầu quan sát thêm để tránh block nhầm. Các bước kiểm tra tiếp theo thường gồm:

- 1) Kiểm tra số cửa sổ liên tiếp (consecutive windows):
  - Lưu lịch sử `attack_score` theo nguồn (ví dụ lưu 5 cửa sổ gần nhất). Nếu có >= `N` cửa sổ liên tiếp vượt `LOW_THR` thì nâng cấp hành động (escalate).

- 2) Kiểm tra xu hướng (moving average / slope):
  - Tính trung bình hoặc độ dốc của `attack_score` trên K cửa sổ; nếu giá trị tăng nhanh (trend dương) và vượt ngưỡng, coi là dấu hiệu tấn công đang leo thang.

- 3) Xác nhận theo family (nếu có):
  - Nếu `stage2` trả về `attack_family` với `family_prob` cao (ví dụ ddos), thì hạ ngưỡng cần để escalate — vì một số kiểu tấn công cần phản ứng nhanh hơn.

- 4) Kiểm tra thêm theo heuristics thô:
  - Nếu các chỉ số như `pkt_count`, `syn_rate`, `dst_port_entropy` vượt ngưỡng cứng, có thể escalate mà không cần nhiều cửa sổ.

- 5) Ngưỡng tối thiểu và whitelist:
  - Bỏ qua mẫu quá nhỏ (`pkt_count < MIN_PKTS`) để tránh tiếng ồn.
  - Nếu `source_ip` nằm trong `whitelist`, không escalate.

- 6) Hành động mềm trước khi cứng:
  - Trước khi block hoàn toàn, áp `throttle` (giới hạn pps/bandwidth) để giảm tác hại, nếu tấn công tiếp tục mới block.

Ví dụ tham số khởi điểm cho người mới:
- `LOW_THR = 0.7`, `HIGH_THR = 0.95`, `THROTTLE_THR = 0.6`
- `N = 2` (2 cửa sổ liên tiếp để escalate)
- `K = 5` (độ dài moving average)
- `MIN_PKTS = 10`, `max_auto_block = 100`

Ví dụ pseudocode đơn giản cho chế độ theo dõi:

```python
recent_scores = deque(maxlen=K)  # per source
recent_scores.append(attack_score)

if attack_score >= HIGH_THR and not whitelisted:
    action = 'block'
elif sum(1 for s in recent_scores if s >= LOW_THR) >= N:
    action = 'block'
elif mean(recent_scores) >= LOW_THR and family_prob >= FAMILY_CONFIDENCE:
    action = 'block'
elif attack_score >= THROTTLE_THR:
    action = 'throttle'
else:
    action = 'monitor'
```

Ghi chú cho người mới: mục tiêu của cơ chế này là cân bằng giữa phát hiện sớm và giảm false-positive — dùng nhiều tín hiệu (ML + heuristic + trend) trước khi chặn một thiết bị.


  ## 7. Logic quyết định & pseudocode
- Tham số mặc định ví dụ:
  - `MIN_PKTS = 10`
  - `THROTTLE_THR = 0.6`
  - `LOW_THR = 0.7`
  - `HIGH_THR = 0.95`
  - `N = 2` (consecutive windows)

Pseudocode tóm tắt:

```python
model_prob = model.predict_proba(x)[0,1]
stat_score = compute_stat_score(features)
attack_score = 0.8*model_prob + 0.2*stat_score

if features['pkt_count'] < MIN_PKTS:
    return 'normal'

if attack_score >= HIGH_THR:
    action = 'block'
elif attack_score >= LOW_THR:
    if positive_in_last_N_windows >= N:
        action = 'block'
    else:
        action = 'throttle'
elif attack_score >= THROTTLE_THR:
    action = 'throttle'
else:
    action = 'none'

# Whitelist check and rate-limit safety
if source_ip in whitelist_ips:
    action = 'none'

# Execute action: ipset add / tc limit / log / emit
```

`compute_stat_score(features)` (gợi ý):
```python
pkt_rate_norm = min(1.0, features['pkt_count'] / PKT_RATE_THRESHOLD)
syn_rate_norm = min(1.0, features['syn_count'] / SYN_THRESHOLD)
entropy_norm = min(1.0, dst_port_entropy / ENTROPY_THRESHOLD)
stat_score = 0.6*pkt_rate_norm + 0.3*syn_rate_norm + 0.1*entropy_norm
```

## 8. Thuật toán & giải thích
- Stage 1: RandomForest hoặc LightGBM (binary)
  - RandomForest: ensemble nhiều cây, robust với outliers, không cần scale nặng, inference nhanh ở scale nhỏ.
  - LightGBM: gradient boosting, thường cho hiệu năng cao hơn, nhanh hơn khi nhiều dữ liệu.
- Stage 2: LightGBM multiclass để phân loại family nếu cần.
- Kết hợp heuristics (`stat_score`) làm ensemble nhẹ để bắt pattern thô (ví dụ packet burst) mà mô hình ML có thể bỏ qua.

Tại sao dùng tree-based models:
- Tốc độ inference tốt trên CPU (không cần GPU).
- Tolerant với scale/feature non-linear.
- Dễ serialize bằng `joblib` và load ở runtime.

## 9. Blocking implementation (kỹ thuật)
- Tối ưu: dùng `ipset` + `iptables` để DROP theo danh sách IP (nhanh hơn thêm rule iptables mỗi IP).
- Thao tác mẫu (xem `docs/IDS_DDoS_Workflow.md`):
```bash
sudo ipset create ids_block hash:ip maxelem 65536
sudo iptables -I INPUT -m set --match-set ids_block src -j DROP
sudo ipset add ids_block 192.168.4.101
# remove
sudo ipset del ids_block 192.168.4.101
```
- Cleanup worker: một tiến trình/cron kiểm tra `blockstore` và xóa entry khi hết `expiry`.

## 10. Testing checklist
- Run `ids_gateway` in monitor-only mode (no block) for 30 min baseline.
- Run `python scripts/attack_sim/simulate_ddos.py` (lab-only) and verify detection, event JSON and `ipset` update.
- Run `train/evaluate_ids_model.py` để sweep threshold and check metrics.

## 11. Monitoring & logging
- Export metrics: inference latency, attack_score histogram, blocked_ips_count → Prometheus
- Logs: structured JSON events (timestamp, src_ip, action, evidence, model_scores)
- Alerts: send webhook to Nodered / PagerDuty for high-severity incidents.

## 12. Safety & rollback
- Use `whitelist_ips` to protect infra devices.
- Limit auto-block count (`max_auto_block`).
- Provide operator UI or CLI for manual unblock.

## 13. Troubleshooting
- If model not loading: kiểm tra paths trong `configs/ids_config.yaml`.
- Nếu false positives cao: chạy `train/evaluate_ids_model.py` với threshold sweep và giảm threshold/hoặc tăng consecutive windows.
- Nếu model inference quá chậm: switch RandomForest → LightGBM or reduce `n_estimators`/`max_depth`.

