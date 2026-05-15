# Kế hoạch phát triển mô hình AI cho IDS Gateway

Phiên bản: 1.0
Ngày: 2026-05-10

## Mục tiêu
- Xây dựng mô hình phân loại nhị phân (normal vs attack) tối ưu cho phát hiện Port-scan và DDoS trên gateway.
- Đạt recall_attack ≥ 0.95 và false positive rate ≤ 3% trong môi trường kiểm thử.
- Triển khai mô hình nhẹ, chạy realtime trên Raspberry Pi (inference ≤ 50 ms/host/window).

## Dữ liệu (Data)
- Nguồn: CIC-DDoS2019 (Kaggle, parquet), BoT-IoT / TON_IoT (CSV), dữ liệu thu thập tại gateway (PCAP -> features).
- Lưu trữ: `data/raw/` và `data/processed/ids_features.csv`.
- Nhãn: `label` (0 normal, 1 attack). Khi có `attack_family` dùng để kiểm thử theo kịch bản DDoS.

## Tiền xử lý (Preprocessing)
- Hợp nhất nhiều file nguồn, chuẩn hóa tên cột, loại bỏ hàng corrupt.
- Chuyển các file Parquet/CSV sang schema chung.
- Lựa chọn mẫu (sampling) và cân bằng lớp bằng `target_attack_ratio` khi cần.
- Lưu bộ features đã chuẩn hóa vào `data/processed/ids_features.csv`.

## Feature engineering
- Các feature ban đầu (đã sử dụng): pkt_count, tcp_count, udp_count, icmp_count, syn_count, ack_count, rst_count, unique_dst_port_count, avg_pkt_len, mean_inter_arrival_ms.
- Bổ sung đề xuất: bytes_per_second, packets_per_second, entropy_dst_ports, top_n_dst_ports_fraction.
- Chuẩn hóa numeric features bằng `StandardScaler` (lưu file scaler vào `models/feature_scaler.joblib`).

## 3. Edge Model Training and Validating

### 3.1. Chuẩn bị dữ liệu (Data Preparation)
- **Bước 1**: Chạy `train/prepare_ids_dataset.py` để hợp nhất CIC-DDoS2019 + BoT-IoT
  - Đầu vào: Raw CSV từ `data/raw/`
  - Đầu ra: `data/processed/ids_features.csv` (chuẩn hóa cột, loại bỏ null, cân bằng lớp)
  - Kỳ vọng: ~5000-10000 hàng, ~60% attack ratio cho balanced training
- **Bước 2**: Kiểm tra dữ liệu đầu ra
  ```bash
  head -5 data/processed/ids_features.csv
  wc -l data/processed/ids_features.csv
  ```

### 3.2. Huấn luyện mô hình (Model Training)
- **Cấu hình**: RandomForest (mặc định) hay LightGBM (tùy chọn)
  - RandomForest: n_estimators=300, max_depth=16, class_weight='balanced'
  - LightGBM: n_estimators=250, max_depth=10, learning_rate=0.05
- **Thực thi**: Chạy `train/train_ids_model.py`
  ```bash
  cd train && python train_ids_model.py --model random_forest --test_size 0.2
  ```
- **Đầu ra**:
  - `models/ids_model.joblib` (mô hình đã train)
  - `models/feature_scaler.joblib` (StandardScaler fitted)
  - Training logs: accuracy, ROC-AUC trên train/test split
- **Checkpoint**: Model phải đạt ROC-AUC ≥ 0.95 trước khi tiếp tục

### 3.3. Xác nhận hiệu suất (Validation)
- **Chạy đánh giá**: `train/evaluate_ids_model.py`
  ```bash
  python evaluate_ids_model.py --model_path models/ids_model.joblib --data_path data/processed/ids_features.csv
  ```
- **Metrics cần đạt**:
  - Recall_attack ≥ 0.95 (phát hiện ít nhất 95% cuộc tấn công)
  - Precision_attack ≥ 0.90 (tối đa 10% false positive)
  - ROC-AUC ≥ 0.97 (phân biệt tốt normal vs attack)
  - False positive rate ≤ 3%
- **Đầu ra**: 
  - `reports/evaluation_summary.json` (detailed metrics)
  - `reports/threshold_sweep.csv` (ngưỡng tối ưu)
  - Confusion matrix visualize

### 3.4. Điều chỉnh ngưỡng (Threshold Tuning)
- **Mục tiêu**: Cân bằng recall vs precision dựa trên acceptance criteria
- **Quy trình**:
  1. Xem `reports/threshold_sweep.csv` để chọn threshold tối ưu (e.g., 0.7)
  2. Cập nhật `configs/ids_config.yaml`: `decision_threshold: 0.7`
  3. Chạy lại evaluate với threshold mới, xác nhận metrics
- **Checkpoint**: Precision và Recall phải thỏa mãn ở ngưỡng được chọn

### 3.5. Kiểm thử kịch bản (Scenario Testing)
- **Kiểm thử Baseline** (Normal traffic):
  ```bash
  # Chạy ESP32 mock data trong 10 phút, kiểm tra Pi nhận được MQTT
  # Verify no false alerts
  ```
- **Kiểm thử Port-Scan**:
  ```bash
  nmap -sS -T4 -p 1-1024 192.168.4.10  # Từ attacker node
  # Verify detect time < 5s, IDS block attacker IP
  ```
- **Kiểm thử DDoS** (UDP flood):
  ```bash
  python scripts/attack_sim/simulate_ddos.py \
    --target-ip 192.168.4.10 --mode udp --duration 60 \
    --workers 4 --rate 200 --i-understand-lab-only
  # Verify detect time < 10s, block attacker, no ESP32 disruption
  ```
- **Kiểm thử Cross-Dataset**:
  - Train trên CIC-DDoS2019, test trên BoT-IoT samples
  - Generalization ROC-AUC phải ≥ 0.90

### 3.6. Chấp nhận và Ký nhận (Acceptance)
- **Tiêu chí chấp nhận mô hình**:
  1. ✅ Recall_attack ≥ 0.95
  2. ✅ Precision_attack ≥ 0.90
  3. ✅ ROC-AUC ≥ 0.97
  4. ✅ Inference latency ≤ 50 ms/prediction trên Pi
  5. ✅ Phát hiện port-scan trong < 5s
  6. ✅ Phát hiện DDoS trong < 10s
- **Ký tên & ghi lại**: 
  - `reports/evaluation_summary.json` đính kèm acceptance checklist
  - Commit hash và model version lên Git
  - Document threshold, hyperparameters sử dụng

## Lựa chọn mô hình & huấn luyện (Chi tiết cấu hình)
- Hệ thống hỗ trợ: `random_forest` (mặc định) và `lightgbm` (tùy chọn) — cả hai đều là tree-based ensemble.
- Cấu hình mặc định:
  - RandomForest: n_estimators=300, max_depth=16, class_weight='balanced'
  - LightGBM: n_estimators=250, max_depth=10, learning_rate=0.05
- Quy trình huấn luyện:
  1. Load `data/processed/ids_features.csv`
  2. Train/test split stratified (configurable test_size)
  3. Scale features
  4. Fit model, xuất `models/ids_model.joblib` và `models/feature_scaler.joblib`

## Đánh giá & chỉ số chấp nhận
- Metrics cần report: precision/recall/F1 (class=attack), ROC-AUC, PR-AUC.
- Threshold sweep: sinh `reports/threshold_sweep.csv` để chọn ngưỡng hoạt động phù hợp.
- Acceptance criteria (mặc định):
  - Recall_attack ≥ 0.95
  - Precision_attack ≥ 0.90 (tùy trade-off)
  - False positive rate ≤ 0.03
  - ROC-AUC ≥ 0.97

## Kịch bản kiểm thử (Test scenarios)
- Baseline: nhiều node ESP32 tạo traffic hợp lệ (mock data) trong 30 phút.
- Port-scan: chạy `nmap -sS -T4` từ attacker node, kiểm tra detect rate.
- DDoS SYN/UDP flood: dùng `scripts/attack_sim/simulate_ddos.py` (udp/tcp/mixed profiles).
- Cross-dataset test: train trên CIC-DDoS2019, kiểm thử trên BoT-IoT/TON_IoT và ngược lại.

## Triển khai (Deployment)
- Xuất model `models/ids_model.joblib` và scaler `models/feature_scaler.joblib`.
- Copy artifacts lên Pi: `/home/pi/project/models/`.
- Runtime: `ids_gateway` module loads scaler+model, inference theo window, threshold cấu hình trong `configs/ids_config.yaml`.
- Làm service systemd: unit file chạy `python -m ids_gateway.main` và auto-restart.

## Giám sát & vận hành
- Logging: lưu score events và block events vào file/Influx/Grafana (tùy chọn).
- Health checks: periodic self-test (synthetic scenario) để xác nhận model còn hoạt động.
- Metrices cần theo dõi: requests/sec, inference latency, blocked IPs, false positive alerts per hour.

## Rủi ro & Biện pháp giảm thiểu
- Overfitting: dùng cross-dataset validation, regularization, giới hạn max_depth.
- Data shift: triển khai periodic retraining nếu traffic runtime drift vượt ngưỡng.
- False positives: dùng whitelist (trusted IP/MAC) và soft-block (throttle) trước full DROP.

## Timeline & Milestones (gợi ý)
- Week 1: Chuẩn hóa dữ liệu & pipeline preprocess
- Week 2: Feature engineering & baseline RandomForest train
- Week 3: Threshold tuning + scenario tests (port-scan, DDoS)
- Week 4: Optimize, export model, deploy to Pi, run endurance test

## Deliverables
- `data/processed/ids_features.csv` (final dataset)
- `models/ids_model.joblib`, `models/feature_scaler.joblib`
- `reports/` (evaluation_summary.json, threshold_sweep.csv, scenario_breakdown.csv)
- Documentation: `docs/IDS_Model_Plan.md` (this file)

---

If you want this translated to English or expanded into step-by-step runbook (commands + expected outputs), tell me which target audience (developer / ops / thesis) and I will produce it.
