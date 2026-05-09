#include <WiFi.h>

#include <PubSubClient.h>

#include <esp_now.h>

const char* ssid = "Umesh";

const char* password = "Umesh123@";

const char* mqtt_server = "demo.thingsboard.io";

const char* token = "QAu646YtjhqqLumP67AS";

uint8_t node1Address[] = {0x00, 0x70, 0x07, 0xE1, 0xFD, 0xD0};

#define TRIG_PIN 25

#define ECHO_PIN 13

#define PUMP_PIN 12

#define MIN_DISTANCE_CM 2

#define MAX_DISTANCE_CM 15

WiFiClient espClient;

PubSubClient client(espClient);

esp_now_peer_info_t peerInfo;

void OnDataSent(const esp_now_send_info_t* info, esp_now_send_status_t status) {

  Serial.print("[ESP-NOW] Send Status: ");

  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");

}

void setupWiFi() {

  Serial.print("Connecting to WiFi");

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {

    delay(500);

    Serial.print(".");

  }

  Serial.println("\nWiFi connected.");

  Serial.print("[Node 2 MAC]: ");

  Serial.println(WiFi.macAddress());

}

void reconnectTB() {

  while (!client.connected()) {

    if (client.connect("ESP32_Node2", token, NULL)) {

      Serial.println("Connected to ThingsBoard");

    } else {

      delay(5000);

    }

  }

}

float getDistanceCM() {

  digitalWrite(TRIG_PIN, LOW);

  delayMicroseconds(2);

  digitalWrite(TRIG_PIN, HIGH);

  delayMicroseconds(10);

  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000);

  if (duration == 0) return 999.0;

  return (duration * 0.0343) / 2.0;

}

void setup() {

  Serial.begin(115200);

  pinMode(TRIG_PIN, OUTPUT);

  pinMode(ECHO_PIN, INPUT);

  pinMode(PUMP_PIN, OUTPUT);

  // Make sure pump is OFF at start (HIGH = OFF for active-low relay)

  digitalWrite(PUMP_PIN, HIGH);

  Serial.println("[PUMP] Initialized OFF");

  setupWiFi();

  client.setServer(mqtt_server, 1883);

  if (esp_now_init() != ESP_OK) {

    Serial.println("[ESP-NOW] Error initializing!");

    return;

  }

  esp_now_register_send_cb(OnDataSent);

  memcpy(peerInfo.peer_addr, node1Address, 6);

  peerInfo.channel = WiFi.channel();

  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {

    Serial.println("[ESP-NOW] Failed to add peer!");

    return;

  }

  Serial.println("--- NODE 2 SANITIZER READY ---");

}

void loop() {

  if (!client.connected()) reconnectTB();

  client.loop();

  float distance = getDistanceCM();

  Serial.print("[Ultrasonic] Distance: ");

  Serial.print(distance);

  Serial.println(" cm");

  // Detect only within 2–15 cm range

  bool detected = (distance >= MIN_DISTANCE_CM && distance <= MAX_DISTANCE_CM);

  if (detected) {

    Serial.println("[Ultrasonic] Hand detected! Triggering pump...");

    // Send ESP-NOW signal to Node 1

    uint8_t signal = 1;

    esp_err_t result = esp_now_send(node1Address, &signal, sizeof(signal));

    if (result == ESP_OK) {

      Serial.println("[ESP-NOW] Signal sent to Node 1.");

    } else {

      Serial.println("[ESP-NOW] Send error!");

    }

    // Turn pump ON (LOW = ON for active-low relay)

    Serial.println("[PUMP] Turning ON...");

    digitalWrite(PUMP_PIN, LOW);

    sendTelemetry(true, 1, distance);

    delay(400); // Pump runs for 400ms

    // Turn pump OFF

    Serial.println("[PUMP] Turning OFF...");

    digitalWrite(PUMP_PIN, HIGH);

    sendTelemetry(false, 0, distance);

    delay(2000); // Cooldown before next detection

  } else {

    digitalWrite(PUMP_PIN, HIGH); // Ensure pump stays OFF

  }

  delay(100);

}

void sendTelemetry(bool triggered, int pStatus, float dist) {

  String telemetry = "{\"ultrasonic_triggered\":" + String(triggered ? "true" : "false") +

                     ",\"pump_status\":" + String(pStatus) +

                     ",\"distance_cm\":" + String(dist, 1) + "}";

  client.publish("v1/devices/me/telemetry", telemetry.c_str());

  Serial.println("Sent: " + telemetry);

}