## Intrusion Detection System for IoT Gateway
### I. Overview
Transform Pi board into a secure IoT Gateway that acts as the first line of defense for the IoT network.
The gateway will continuously monitor network traffic (specifically packet headers) from all connected sensor and end devices. Using rule-based or lightweight anomaly detection, it will identify suspicious behavior such as DDoS attacks or port scanning originating from compromised devices. Upon detection, the gateway will automatically isolate and block the offending device to prevent it from disrupting the network or launching further attacks. 

This project provides a practical, hands-on approach to implementing network security at the edge of an IoT ecosystem.

---
### II. Roles
#### 1. Infrastructure Setup
* Setup Raspberry PI and ESP32 board.
* Setup Infrastructure to run the application
* Concept:
    * Stack in Gateway
        1. Using MQTT broker and Suricata to guard and gather data and packet information from sensor
        2. MQTT Broker subscribe to Nodered which is a service to apply business policy such as validation and security 
        3. Promtail to gather data from MQTT broker, Nodered and Suricata logs and transmit to Host device data source
    * Stack in Host Device

        1. Loki acted as data source to collect data from Promtail through IP with SSL Verification
        2. Grafana Dashboard will use data in Loki to visualize

    * CI/CD stack:

        1. Automated Action to clean log file in gateway to save resources
        2. Deploy changes into deployment 
* Assigned Student: Nguyen Minh Bach

---
#### 2. Monitoring and Pipeline Setup
* Creating Dashboard to monitor data from the traffic and network
* Create Data Pipeline to process data based on security and validation policy 
* Assigned Student: Nguyen Tran Son Tung

---
#### 3. Edge Model Training and Validating
* Prepare dataset from CIC-DDoS2019 + BoT-IoT sources using `train/prepare_ids_dataset.py`
* Train RandomForest model (n_estimators=300, max_depth=16) with `train/train_ids_model.py`
* Validate acceptance criteria: Recall ≥0.95, Precision ≥0.90, ROC-AUC ≥0.97
* Test scenarios: baseline traffic, port-scan detection, DDoS flood detection
* Accept model when all 6 criteria met (metrics + latency + detection time)
* Assigned Student: Pham Huynh Tri Thuc