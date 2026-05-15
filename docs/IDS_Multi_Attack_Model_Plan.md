# Kế hoạch mô hình IDS đa kiểu tấn công cho Gateway Edge

Phiên bản: 1.0  
Ngày: 2026-05-14

## 1. Mục tiêu mở rộng
- Chuyển từ bài toán nhị phân (normal vs attack) sang phát hiện và phân loại đa kiểu tấn công.
- Hỗ trợ ít nhất các nhóm tấn công: DDoS, PortScan, Recon, BruteForce, Botnet, WebAttack.
- Vẫn đảm bảo khả năng chạy thời gian thực trên Raspberry Pi.

## 2. Hiện trạng đã kiểm tra trong project

### 2.1 Dataset và kết quả hiện tại
- File huấn luyện hiện tại: data/processed/ids_features.csv
- Quy mô hiện tại: 5098 dòng, attack_ratio khoảng 0.60
- Báo cáo hiện tại (reports/evaluation_summary.json):
  - ROC-AUC: 0.9936
  - Precision_attack: 0.9871
  - Recall_attack: 0.9984
- reports/scenario_breakdown.csv cho thấy attack_family hiện tại chủ yếu là:
  - ddos
  - normal

### 2.2 Kiến trúc model hiện tại
- Pipeline hiện tại trong train/train_ids_model.py:
  - StandardScaler
  - RandomForest (mặc định) hoặc LightGBM
- Feature hiện tại trong configs/ids_config.yaml: 10 feature
  - pkt_count, tcp_count, udp_count, icmp_count
  - syn_count, ack_count, rst_count
  - unique_dst_port_count
  - avg_pkt_len, mean_inter_arrival_ms
- Nhãn hiện tại: label (0/1), có cột attack_family nhưng chưa huấn luyện multi-class đầy đủ.

## 3. Dữ liệu để mở rộng nhiều loại tấn công

### 3.1 Dataset ưu tiên sử dụng
- Có sẵn trong workspace:
  - CIC-DDoS2019 (parquet trong data/raw/cicddos2019)
  - Bộ file CSV trong data/raw (data_1.csv ... data_74.csv)
- Nên bổ sung thêm để mở rộng attack families:
  - CIC-IDS2017: PortScan, BruteForce, Web Attack, Botnet, DoS
  - CSE-CIC-IDS2018: Infiltration, Botnet, BruteForce, DoS/DDoS, Web attacks
  - TON_IoT hoặc UNSW-NB15: bổ sung lưu lượng normal và tấn công mạng IoT tổng quát

### 3.2 Mapping nhãn thống nhất
Dùng taxonomy thống nhất trong pipeline:
- normal
- ddos
- portscan
- recon
- brute_force
- botnet
- web_attack
- attack_other

Mapping này nên được thực hiện trong train/prepare_ids_dataset.py để tất cả dataset về chung schema.

## 4. Kiến trúc model để xử lý multi-attack

### 4.1 Kiến trúc đề xuất (2 tầng)
- Tầng 1 (Gate model): bộ phân loại nhị phân normal vs attack
  - Mục tiêu: bắt attack sớm, độ nhạy cao
- Tầng 2 (Family classifier): bộ phân loại đa lớp cho traffic đã được đánh dấu attack
  - Mục tiêu: xác định loại tấn công để phản ứng đúng chính sách

Lý do chọn 2 tầng:
- Giảm false negative ở tầng 1
- Tầng 2 có thể tối ưu riêng theo từng attack family
- Dễ debug và theo dõi hơn một model multi-class đơn lẻ

### 4.2 Lựa chọn model khuyến nghị
- Tầng 1:
  - RandomForest hoặc LightGBM để giữ tương thích với code hiện tại
- Tầng 2:
  - LightGBM multiclass, ưu tiên vì nhẹ và nhanh
  - Fallback: RandomForest multi-class nếu cần độ ổn định cao

## 5. Thông số huấn luyện khuyến nghị

### 5.1 Data split và validation
- Không chỉ split ngẫu nhiên; ưu tiên group/time-aware split theo source day để tránh leakage.
- Tỷ lệ gợi ý:
  - Train: 70%
  - Validation: 15%
  - Test: 15%
- Bắt buộc có cross-dataset test (train A, test B).

### 5.2 Hyperparameters khởi đầu
Tầng 1 (binary):
- RandomForest:
  - n_estimators: 300-500
  - max_depth: 12-20
  - min_samples_split: 4-12
  - class_weight: balanced
- LightGBM:
  - n_estimators: 200-500
  - max_depth: 8-12
  - learning_rate: 0.03-0.08
  - num_leaves: 31-63

Tầng 2 (multiclass):
- LightGBM multiclass:
  - objective: multiclass
  - num_class: số attack families + normal (hoặc chỉ families attack nếu dùng 2 tầng)
  - n_estimators: 250-600
  - max_depth: 8-12
  - class_weight: balanced (hoặc sample weighting theo family)

### 5.3 Threshold và decision policy
- Tầng 1: dùng threshold riêng (ví dụ 0.6-0.8) tùy theo yêu cầu false positive.
- Tầng 2: dùng max-probability + reject option:
  - Nếu score thấp hơn ngưỡng family -> gán attack_other để tránh phân loại sai mạnh.

## 6. Kiến trúc feature có thể bổ sung

Ngoài 10 feature hiện tại, nên bổ sung theo cửa sổ thời gian:
- bytes_per_second
- packets_per_second
- syn_rate
- rst_rate
- dst_port_entropy
- src_ip_entropy
- unique_dst_ip_count
- tcp_handshake_success_ratio

Nguyên tắc:
- Feature phải tính được từ packet headers trên gateway
- Tránh feature cần payload để giảm tài nguyên và tránh vấn đề riêng tư

## 7. Metrics và acceptance cho multi-attack

### 7.1 Tầng 1 (binary)
- Recall_attack >= 0.97
- Precision_attack >= 0.92
- FPR <= 0.03

### 7.2 Tầng 2 (family classification)
- Macro-F1 >= 0.85
- Weighted-F1 >= 0.90
- Recall cho từng family quan trọng (ddos, portscan, brute_force) >= 0.85

### 7.3 Edge runtime
- Inference latency:
  - Tầng 1 <= 50 ms/window/host
  - Tầng 2 <= 80 ms/window/host
- CPU gateway trung bình <= 70% khi load test

## 8. Kịch bản test cần có
- Normal-only endurance: 6-12 giờ không có false alert đáng kể
- PortScan test (nmap)
- DDoS test (udp/tcp/mixed flood)
- BruteForce simulation (SSH login burst có giới hạn trong lab)
- Recon test (service/version scan)
- Cross-dataset evaluation (train trên dataset A, test dataset B)

## 9. Kế hoạch cập nhật code
- train/prepare_ids_dataset.py:
  - Mở rộng mapping attack_family cho brute_force, botnet, web_attack
  - Thêm thống kê phân bố từng family
- train/train_ids_model.py:
  - Thêm chế độ train tầng 1 và tầng 2
  - Lưu 2 artifact: model_stage1.joblib, model_stage2.joblib
- train/evaluate_ids_model.py:
  - Báo cáo thêm macro/weighted F1 và confusion matrix cho attack families
- configs/ids_config.yaml:
  - Thêm config cho stage1_threshold, stage2_threshold, enabled_families

## 10. Deliverables để xác nhận
- data/processed/ids_features_multiattack.csv
- models/model_stage1.joblib
- models/model_stage2.joblib
- reports/multiattack_eval_summary.json
- reports/multiattack_family_breakdown.csv
- Tài liệu vận hành và acceptance checklist multi-attack
