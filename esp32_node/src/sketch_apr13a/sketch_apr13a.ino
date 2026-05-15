// #include <WiFi.h>
// #include <PubSubClient.h>
// #include <DHT.h>
// #include <time.h>

// // ============================================================================
// // CONFIGURATION SECTION (MODIFY THESE VALUES)
// // ============================================================================

// // WiFi Credentials
// const char* WIFI_SSID = "IoT-Gateway";           // Your WiFi SSID
// const char* WIFI_PASSWORD = "12345678";        // Your WiFi Password

// // MQTT Broker Configuration
// const char* MQTT_SERVER = "192.168.4.1";        // Pi's IP address
// const int MQTT_PORT = 1883;                      // Standard MQTT port
// const char* MQTT_CLIENTID = "esp32-node-01";    // Unique identifier
// const char* MQTT_TOPIC_DATA = "sensor/esp32-01/data";
// const char* MQTT_TOPIC_STATUS = "sensor/esp32-01/status";

// // DHT Sensor Configuration
// #define DHT_PIN 4                                 // GPIO4
// #define DHT_TYPE DHT22                            // DHT22 sensor type

// // Timing Configuration
// const unsigned long PUBLISH_INTERVAL = 15000;    // Publish every 15 seconds
// const unsigned long RECONNECT_INTERVAL = 5000;   // Reconnect attempt every 5 seconds
// const unsigned long NTP_UPDATE_INTERVAL = 3600000; // Update time every 1 hour

// // ============================================================================
// // GLOBAL OBJECTS
// // ============================================================================

// WiFiClient espClient;
// PubSubClient client(espClient);
// DHT dht(DHT_PIN, DHT_TYPE);

// unsigned long lastPublishTime = 0;
// unsigned long lastReconnectTime = 0;
// unsigned long lastNtpUpdateTime = 0;
// int reconnectAttempts = 0;

// // ============================================================================
// // FUNCTION: Initialize Serial Logging
// // ============================================================================

// void setup_serial() {
//   Serial.begin(115200);
//   delay(1000);

//   Serial.println("\n\n================================");
//   Serial.println("ESP32 IoT Node - Startup");
//   Serial.println("================================");
//   Serial.printf("Firmware Version: 1.0.0\n");
//   Serial.printf("Compile Date: %s %s\n", __DATE__, __TIME__);
//   Serial.printf("Reset Reason: %s\n", esp_reset_reason() == ESP_RST_POWERON ? "Power-On" : "Other");
// }

// // ============================================================================
// // FUNCTION: Initialize DHT Sensor
// // ============================================================================

// void setup_dht() {
//   Serial.println("[DHT] Initializing DHT22 sensor on GPIO4...");
//   dht.begin();
//   delay(2000);

//   float temp = dht.readTemperature();
//   float humid = dht.readHumidity();

//   if (isnan(temp) || isnan(humid)) {
//     Serial.println("[DHT] WARNING: Could not read from DHT sensor");
//     Serial.println("[DHT] Check connections: VCC->3V3, GND->GND, DATA->GPIO4");
//   } else {
//     Serial.printf("[DHT] Initial read - Temp: %.1fC, Humidity: %.1f%%\n", temp, humid);
//   }
// }

// // ============================================================================
// // FUNCTION: Connect to WiFi with Exponential Backoff
// // ============================================================================

// void connect_wifi() {
//   if (WiFi.status() == WL_CONNECTED) {
//     return;
//   }

//   unsigned long currentTime = millis();
//   unsigned long backoffTime = RECONNECT_INTERVAL * (1 << min(reconnectAttempts, 3));
//   backoffTime = min(backoffTime, 60000UL);

//   if (currentTime - lastReconnectTime < backoffTime) {
//     return;
//   }

//   lastReconnectTime = currentTime;

//   Serial.printf("[WiFi] Attempting connection (attempt %d)...\n", ++reconnectAttempts);
//   Serial.printf("[WiFi] SSID: %s\n", WIFI_SSID);

//   WiFi.mode(WIFI_STA);
//   WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

//   int attempts = 0;
//   while (WiFi.status() != WL_CONNECTED && attempts < 20) {
//     delay(500);
//     Serial.print(".");
//     attempts++;
//   }
//   Serial.println();

//   if (WiFi.status() == WL_CONNECTED) {
//     reconnectAttempts = 0;
//     Serial.printf("[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
//     Serial.printf("[WiFi] RSSI: %d dBm\n", WiFi.RSSI());
//   } else {
//     Serial.printf("[WiFi] Failed. Will retry in %lus\n", backoffTime / 1000);
//   }
// }

// // ============================================================================
// // FUNCTION: MQTT Connect
// // ============================================================================

// void connect_mqtt() {
//   if (client.connected()) {
//     return;
//   }

//   if (WiFi.status() != WL_CONNECTED) {
//     Serial.println("[MQTT] WiFi not connected, skipping MQTT connect");
//     return;
//   }

//   Serial.printf("[MQTT] Attempting connection to %s:%d\n", MQTT_SERVER, MQTT_PORT);

//   if (client.connect(MQTT_CLIENTID)) {
//     Serial.println("[MQTT] Connected!");
//     reconnectAttempts = 0;
//     client.publish(MQTT_TOPIC_STATUS, "online");
//   } else {
//     Serial.printf("[MQTT] Failed, rc=%d\n", client.state());
//     Serial.println("[MQTT] Codes: -4=timeout, -3=lost connection, -2=connect failed, -1=disconnected");
//   }
// }

// // ============================================================================
// // FUNCTION: Synchronize Time via NTP
// // ============================================================================

// void sync_ntp() {
//   unsigned long currentTime = millis();

//   if (currentTime - lastNtpUpdateTime < NTP_UPDATE_INTERVAL) {
//     return;
//   }

//   lastNtpUpdateTime = currentTime;

//   if (WiFi.status() != WL_CONNECTED) {
//     Serial.println("[NTP] WiFi not connected, skipping NTP sync");
//     return;
//   }

//   Serial.println("[NTP] Synchronizing time from NTP server...");
//   configTime(0, 0, "pool.ntp.org", "time.nist.gov");

//   time_t now = time(nullptr);
//   int attempts = 0;
//   while (now < 24 * 3600 && attempts < 20) {
//     delay(500);
//     Serial.print(".");
//     now = time(nullptr);
//     attempts++;
//   }
//   Serial.println();

//   if (now > 24 * 3600) {
//     Serial.printf("[NTP] Time set: %s", ctime(&now));
//   } else {
//     Serial.println("[NTP] Failed to set time");
//   }
// }

// // ============================================================================
// // FUNCTION: Read DHT Sensor and Publish to MQTT
// // ============================================================================

// void publish_sensor_data() {
//   float temperature = dht.readTemperature();
//   float humidity = dht.readHumidity();

//   if (isnan(temperature) || isnan(humidity)) {
//     Serial.println("[SENSOR] Failed to read from DHT sensor");
//     return;
//   }

//   time_t now = time(nullptr);
//   struct tm* timeinfo = localtime(&now);
//   char timestamp[20];
//   strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", timeinfo);

//   char payload[256];
//   snprintf(payload, sizeof(payload),
//     "{\"timestamp\":\"%s\",\"temperature\":%.2f,\"humidity\":%.2f,\"signal_strength\":%d}",
//     timestamp, temperature, humidity, WiFi.RSSI());

//   if (client.publish(MQTT_TOPIC_DATA, payload)) {
//     Serial.printf("[PUBLISH] %s\n", MQTT_TOPIC_DATA);
//     Serial.printf("[PAYLOAD] %s\n", payload);
//   } else {
//     Serial.println("[PUBLISH] Failed - MQTT not connected");
//   }
// }

// // ============================================================================
// // FUNCTION: Print System Status (Debug)
// // ============================================================================

// void print_status() {
//   static unsigned long lastPrintTime = 0;
//   unsigned long currentTime = millis();

//   if (currentTime - lastPrintTime < 60000) {
//     return;
//   }

//   lastPrintTime = currentTime;

//   Serial.println("\n================================");
//   Serial.println("System Status Report");
//   Serial.println("================================");
//   Serial.printf("Uptime: %lu seconds\n", currentTime / 1000);
//   Serial.printf("WiFi Status: %s\n", WiFi.status() == WL_CONNECTED ? "Connected" : "Disconnected");
//   Serial.printf("WiFi IP: %s\n", WiFi.localIP().toString().c_str());
//   Serial.printf("WiFi RSSI: %d dBm\n", WiFi.RSSI());
//   Serial.printf("MQTT Status: %s\n", client.connected() ? "Connected" : "Disconnected");
//   Serial.printf("Heap Free: %d bytes\n", ESP.getFreeHeap());
//   Serial.println("================================\n");
// }

// // ============================================================================
// // SETUP: Called once at startup
// // ============================================================================

// void setup() {
//   setup_serial();
//   setup_dht();

//   client.setServer(MQTT_SERVER, MQTT_PORT);

//   connect_wifi();
//   sync_ntp();
//   connect_mqtt();
// }

// // ============================================================================
// // LOOP: Main execution loop
// // ============================================================================

// void loop() {
//   connect_wifi();
//   sync_ntp();

//   if (!client.connected()) {
//     connect_mqtt();
//   } else {
//     client.loop();
//   }

//   unsigned long currentTime = millis();
//   if (currentTime - lastPublishTime >= PUBLISH_INTERVAL) {
//     lastPublishTime = currentTime;
//     publish_sensor_data();
//   }

//   print_status();
//   delay(100);
// }




#include <WiFi.h>
#include <PubSubClient.h>
#include <time.h>

// ============================================================================
// CONFIGURATION SECTION (MODIFY THESE VALUES)
// ============================================================================

// WiFi Credentials
const char* WIFI_SSID = "IoT-Gateway";           // Your WiFi SSID
const char* WIFI_PASSWORD = "12345678";        // Your WiFi Password

// MQTT Broker Configuration
const char* MQTT_SERVER = "192.168.4.1";        // Pi's IP address
const int MQTT_PORT = 1883;                      // Standard MQTT port
const char* MQTT_CLIENTID = "esp32-node-01";    // Unique identifier
const char* MQTT_TOPIC_DATA = "sensor/esp32-01/data";
const char* MQTT_TOPIC_STATUS = "sensor/esp32-01/status";


// Timing Configuration
const unsigned long PUBLISH_INTERVAL = 15000;    // Publish every 15 seconds
const unsigned long RECONNECT_INTERVAL = 5000;   // Reconnect attempt every 5 seconds
const unsigned long NTP_UPDATE_INTERVAL = 3600000; // Update time every 1 hour

// ============================================================================
// GLOBAL OBJECTS
// ============================================================================

WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastPublishTime = 0;
unsigned long lastReconnectTime = 0;
unsigned long lastNtpUpdateTime = 0;
int reconnectAttempts = 0;

// ============================================================================
// FUNCTION: Initialize Serial Logging
// ============================================================================

void setup_serial() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n\n================================");
  Serial.println("ESP32 IoT Node - Normal Traffic Generator");
  Serial.println("================================");
  Serial.printf("Firmware Version: 2.0.0 (Mock Data - No DHT)\n");
  Serial.printf("Compile Date: %s %s\n", __DATE__, __TIME__);
  Serial.printf("Reset Reason: %s\n", esp_reset_reason() == ESP_RST_POWERON ? "Power-On" : "Other");
}

// ============================================================================
// FUNCTION: Connect to WiFi with Exponential Backoff
// ============================================================================

void connect_wifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  unsigned long currentTime = millis();
  unsigned long backoffTime = RECONNECT_INTERVAL * (1 << min(reconnectAttempts, 3));
  backoffTime = min(backoffTime, 60000UL);

  if (currentTime - lastReconnectTime < backoffTime) {
    return;
  }

  lastReconnectTime = currentTime;

  Serial.printf("[WiFi] Attempting connection (attempt %d)...\n", ++reconnectAttempts);
  Serial.printf("[WiFi] SSID: %s\n", WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    reconnectAttempts = 0;
    Serial.printf("[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[WiFi] RSSI: %d dBm\n", WiFi.RSSI());
  } else {
    Serial.printf("[WiFi] Failed. Will retry in %lus\n", backoffTime / 1000);
  }
}

// ============================================================================
// FUNCTION: MQTT Connect
// ============================================================================

void connect_mqtt() {
  if (client.connected()) {
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[MQTT] WiFi not connected, skipping MQTT connect");
    return;
  }

  Serial.printf("[MQTT] Attempting connection to %s:%d\n", MQTT_SERVER, MQTT_PORT);

  if (client.connect(MQTT_CLIENTID)) {
    Serial.println("[MQTT] Connected!");
    reconnectAttempts = 0;
    client.publish(MQTT_TOPIC_STATUS, "online");
  } else {
    Serial.printf("[MQTT] Failed, rc=%d\n", client.state());
    Serial.println("[MQTT] Codes: -4=timeout, -3=lost connection, -2=connect failed, -1=disconnected");
  }
}

// ============================================================================
// FUNCTION: Synchronize Time via NTP
// ============================================================================

void sync_ntp() {
  unsigned long currentTime = millis();

  if (currentTime - lastNtpUpdateTime < NTP_UPDATE_INTERVAL) {
    return;
  }

  lastNtpUpdateTime = currentTime;

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[NTP] WiFi not connected, skipping NTP sync");
    return;
  }

  Serial.println("[NTP] Synchronizing time from NTP server...");
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");

  time_t now = time(nullptr);
  int attempts = 0;
  while (now < 24 * 3600 && attempts < 20) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    attempts++;
  }
  Serial.println();

  if (now > 24 * 3600) {
    Serial.printf("[NTP] Time set: %s", ctime(&now));
  } else {
    Serial.println("[NTP] Failed to set time");
  }
}

// ============================================================================
// FUNCTION: Generate Mock Sensor Data and Publish to MQTT
// ============================================================================

void publish_mock_data() {
  // Generate mock sensor readings (simulating IoT device)
  // Range: temp 20-30°C, humidity 40-70%
  float temperature = 22.5 + (random(0, 100) / 100.0) * 5;
  float humidity = 50.0 + (random(0, 100) / 100.0) * 20;
  int signal_strength = WiFi.RSSI();

  // Get current timestamp
  time_t now = time(nullptr);
  struct tm* timeinfo = localtime(&now);
  char timestamp[20];
  strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", timeinfo);

  // Build JSON payload (simulating legitimate sensor data)
  char payload[256];
  snprintf(payload, sizeof(payload),
    "{\"timestamp\":\"%s\",\"temperature\":%.2f,\"humidity\":%.2f,\"signal_strength\":%d}",
    timestamp, temperature, humidity, signal_strength);

  // Publish to MQTT
  if (client.publish(MQTT_TOPIC_DATA, payload)) {
    Serial.printf("[PUBLISH] %s\n", MQTT_TOPIC_DATA);
    Serial.printf("[DATA] Temp: %.2f C, Humidity: %.2f %%, Signal: %d dBm\n", temperature, humidity, signal_strength);
    Serial.printf("[PAYLOAD] %s\n", payload);
  } else {
    Serial.println("[PUBLISH] Failed - MQTT not connected");
  }
}

// ============================================================================
// FUNCTION: Print System Status (Debug)
// ============================================================================

void print_status() {
  static unsigned long lastPrintTime = 0;
  unsigned long currentTime = millis();

  if (currentTime - lastPrintTime < 60000) {
    return;
  }

  lastPrintTime = currentTime;

  Serial.println("\n================================");
  Serial.println("System Status Report");
  Serial.println("================================");
  Serial.printf("Uptime: %lu seconds\n", currentTime / 1000);
  Serial.printf("WiFi Status: %s\n", WiFi.status() == WL_CONNECTED ? "Connected" : "Disconnected");
  Serial.printf("WiFi IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("WiFi RSSI: %d dBm\n", WiFi.RSSI());
  Serial.printf("MQTT Status: %s\n", client.connected() ? "Connected" : "Disconnected");
  Serial.printf("Heap Free: %d bytes\n", ESP.getFreeHeap());
  Serial.println("================================\n");
}

// ============================================================================
// SETUP: Called once at startup
// ============================================================================

void setup() {
  setup_serial();

  client.setServer(MQTT_SERVER, MQTT_PORT);

  connect_wifi();
  sync_ntp();
  connect_mqtt();
}

// ============================================================================
// LOOP: Main execution loop
// ============================================================================

void loop() {
  connect_wifi();
  sync_ntp();

  if (!client.connected()) {
    connect_mqtt();
  } else {
    client.loop();
  }

  unsigned long currentTime = millis();
  if (currentTime - lastPublishTime >= PUBLISH_INTERVAL) {
    lastPublishTime = currentTime;
    publish_mock_data();
  }

  print_status();
  delay(100);
}

