1. Sinh viên A: Hardware Setup & IoT Nodes Layer (Phần cứng + Thiết bị IoT)

Chọn, lắp ráp và cấu hình phần cứng: Raspberry Pi 3/4 làm Gateway + NodeMCU/ESP32 làm IoT nodes.
Cài đặt OS (Raspberry Pi OS), cấu hình network (routing, bridge, IP forwarding).
Lập trình firmware cho các node ESP32: gửi dữ liệu cảm biến qua MQTT/CoAP, tạo lưu lượng mạng bình thường.
Mô phỏng tấn công từ nodes: DDoS (flood packet), Port Scanning.
Kiểm tra và debug hardware, đảm bảo nodes giao tiếp ổn định với Gateway.
Output: Hệ thống IoT testbed chạy được (nodes → Gateway).

2. Sinh viên B: Network Monitoring & Data Pipeline (Giám sát mạng + Xử lý dữ liệu)

Triển khai packet capture thời gian thực trên Raspberry Pi (sử dụng Scapy, tcpdump hoặc libpcap).
Trích xuất đặc trưng từ packet headers (IP, port, packet size, interval, protocol flags…).
Xây dựng pipeline xử lý dữ liệu: feature engineering, lưu trữ live dataset (normal + attack traffic).
Phân tích giao thức: TCP/IP, MQTT, CoAP trên lưu lượng thực tế.
Xây dựng hệ thống logging và data flow (đẩy dữ liệu vào model).
Output: Module thu thập và xử lý đặc trưng thời gian thực, dataset thực tế từ testbed.

3. Sinh viên C: Machine Learning Model & Detection/Response (Model AI + Phát hiện & Chặn)

Tải và tiền xử lý dataset công khai (BoT-IoT dataset hoặc TON_IoT dataset).
Train, tune, đánh giá model Random Forest hoặc LightGBM trên laptop.
Export model (joblib / ONNX / pickle) và triển khai inference trên Raspberry Pi (Edge AI).
Xây dựng logic phát hiện thời gian thực + ngưỡng cảnh báo.
Triển khai cơ chế block tự động (iptables, firewall rules chặn IP/MAC của node tấn công).
Tối ưu hiệu năng model trên Raspberry Pi (latency, CPU/RAM usage).
Output: Module IDS hoàn chỉnh (inference + block).