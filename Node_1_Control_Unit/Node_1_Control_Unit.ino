#include <WiFi.h>
#include <PubSubClient.h>
#include <esp_now.h>
#include "SoftwareSerial.h"
#include "DFRobotDFPlayerMini.h"
#include <algorithm>

// -------- Configuration --------
const char* ssid = "vivotg";
const char* password = "12345678";
const char* TOKEN = "84oGUFsFReeHJLsmXmoO";
const char* THINGSBOARD_SERVER = "demo.thingsboard.io";
const uint16_t THINGSBOARD_PORT = 1883;

WiFiClient espClient;
PubSubClient client(espClient);

// -------- Pins --------
#define TRIG1 32
#define ECHO1 34
#define TRIG2 33
#define ECHO2 35
#define TRIG3 25
#define ECHO3 26
#define DF_RX 16
#define DF_TX 17

// -------- Logic Settings --------
const int triggerDistance = 50;
const unsigned long windowTime = 20000;      // Timer 1: 20s Compliance Window
const unsigned long audioRepeatTime = 7000;  // Timer 2: 7s Audio Reminder
const int presenceRequired = 5000;
uint8_t camMAC[] = {0xC0, 0xCD, 0xD6, 0x8E, 0xAC, 0x24};

SoftwareSerial mySoftwareSerial(DF_RX, DF_TX);
DFRobotDFPlayerMini myDFPlayer;

int hello = 0;
unsigned long lastMonitorPrint = 0;

// -------- ESP-NOW Sanitizer Signal Flag --------
volatile bool sanitizerSignalReceived = false;

// -------- ESP-NOW Receive Callback --------
void onDataReceived(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len > 0 && data[0] == 1) {
    sanitizerSignalReceived = true;
    Serial.println("[ESP-NOW] Sanitizer signal received from Node 2!");
  }
}

void connectToWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\n[WiFi] Connected.");
}

void connectToMQTT() {
  while (!client.connected()) {
    Serial.print("[Cloud] Connecting to ThingsBoard...");
    if (client.connect("ESP32_NICU", TOKEN, NULL)) {
      Serial.println(" Success!");
      client.publish("v1/devices/me/telemetry", "{\"status\":\"SYSTEM_ONLINE\"}");
    } else {
      Serial.print(" Failed, rc="); Serial.print(client.state());
      delay(5000);
    }
  }
}

// isUS1=true applies *15 scaling (only for US1), isUS1=false gives normal distance
long getDistance(int trig, int echo, bool isUS1) {
  const int samples = 5;
  long readings[samples];
  for (int i = 0; i < samples; i++) {
    digitalWrite(trig, LOW); delayMicroseconds(2);
    digitalWrite(trig, HIGH); delayMicroseconds(10);
    digitalWrite(trig, LOW);
    long duration = pulseIn(echo, HIGH, 35000);
    long base = (duration * 0.034 / 2);
    readings[i] = isUS1 ? (base * 15) : base;
    delay(30);
  }
  std::sort(readings, readings + samples);
  return readings[2];
}

// -------- Check if any sensor detects a person --------
// US1 uses *15 scaling, US2 and US3 use normal distance
bool eitherSensorTriggered() {
  long d1 = getDistance(TRIG1, ECHO1, true);   // scaled *15
  long d2 = getDistance(TRIG2, ECHO2, false);  // normal
  long d3 = getDistance(TRIG3, ECHO3, false);  // normal
  bool t1 = (d1 < triggerDistance && d1 > 5);
  bool t2 = (d2 < triggerDistance && d2 > 1);
  bool t3 = (d3 < triggerDistance && d3 > 1);
  if (t1) Serial.print("[Trigger] US1 hit: "); else Serial.print("[Scan] US1: ");
  Serial.print(d1); Serial.print(" cm | ");
  if (t2) Serial.print("[Trigger] US2 hit: "); else Serial.print("US2: ");
  Serial.print(d2); Serial.print(" cm | ");
  if (t3) Serial.print("[Trigger] US3 hit: "); else Serial.print("US3: ");
  Serial.print(d3); Serial.println(" cm");
  return (t1 || t2 || t3);
}

// -------- Confirm continuous presence using all three sensors (5s rule) --------
bool confirmPresence() {
  unsigned long pStart = millis();
  int glitchCount = 0;

  while (millis() - pStart < presenceRequired) {
    long c1 = getDistance(TRIG1, ECHO1, true);   // scaled *15
    long c2 = getDistance(TRIG2, ECHO2, false);  // normal
    long c3 = getDistance(TRIG3, ECHO3, false);  // normal
    bool seen1 = (c1 < triggerDistance && c1 > 5);
    bool seen2 = (c2 < triggerDistance && c2 > 1);
    bool seen3 = (c3 < triggerDistance && c3 > 1);

    // Person is considered present if at least one sensor still sees them
    if (!seen1 && !seen2 && !seen3) {
      glitchCount++;
    } else {
      glitchCount = 0;
    }

    if (glitchCount >= 4) {
      return false; // Person left
    }
    delay(150);
  }
  return true;
}

void setup() {
  Serial.begin(115200);
  mySoftwareSerial.begin(9600);
  pinMode(TRIG1, OUTPUT); pinMode(ECHO1, INPUT);
  pinMode(TRIG2, OUTPUT); pinMode(ECHO2, INPUT);
  pinMode(TRIG3, OUTPUT); pinMode(ECHO3, INPUT);

  connectToWiFi();
  client.setServer(THINGSBOARD_SERVER, THINGSBOARD_PORT);

  myDFPlayer.begin(mySoftwareSerial, false, false);
  myDFPlayer.volume(15);

  // Init ESP-NOW
  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(onDataReceived);

    // Register camera as peer for sending violation signal
    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, camMAC, 6);
    peerInfo.channel = WiFi.channel();
    peerInfo.encrypt = false;
    esp_now_add_peer(&peerInfo);

    Serial.println("[ESP-NOW] Initialized. Listening for Node 2 signals.");
  } else {
    Serial.println("[ESP-NOW] Init FAILED!");
  }

  Serial.println("--- NICU COMPLIANCE SYSTEM READY ---");
}

void loop() {
  connectToWiFi();
  if (!client.connected()) connectToMQTT();
  client.loop();

  // -------- Idle Monitor --------
  if (millis() - lastMonitorPrint > 2000) {
    long d1_idle = getDistance(TRIG1, ECHO1, true);   // scaled *15
    long d2_idle = getDistance(TRIG2, ECHO2, false);  // normal
    long d3_idle = getDistance(TRIG3, ECHO3, false);  // normal
    hello += 2;
    Serial.print("[Monitor] Idle - US1: "); Serial.print(d1_idle);
    Serial.print(" cm | US2: "); Serial.print(d2_idle);
    Serial.print(" cm | US3: "); Serial.print(d3_idle);
    Serial.print(" cm | Cloud Test (hello): "); Serial.println(hello);
    String payload = "{\"hello\":" + String(hello) + "}";
    client.publish("v1/devices/me/telemetry", payload.c_str());
    lastMonitorPrint = millis();
  }

  // -------- Step 1: Check if ANY sensor sees someone --------
  if (eitherSensorTriggered()) {
    Serial.println("\n[!] TARGET SPOTTED (US1, US2, or US3). Starting 5s Confirmation...");

    bool stayed = confirmPresence();

    if (stayed) {
      Serial.println(">>> PERSON CONFIRMED. Sending to Cloud & Playing Audio 1...");
      client.publish("v1/devices/me/telemetry", "{\"status\":\"PERSON_DETECTED\"}");
      myDFPlayer.play(1);

      // -------- Step 2: Reset sanitizer flag, start compliance window --------
      sanitizerSignalReceived = false;
      unsigned long wStart = millis();
      bool repeatPlayed = false;
      unsigned long lastWindowPrint = 0;

      Serial.println(">>> WAITING FOR NODE 2 SANITIZER SIGNAL (20s Window) | Reminder at 7s...");

      while (millis() - wStart < windowTime) {
        client.loop(); // Keep MQTT alive during window

        // Timer 2: Repeat Audio 1 at 7s if not yet sanitized
        if (!repeatPlayed && (millis() - wStart >= audioRepeatTime)) {
          Serial.println(">>> 7s REMINDER: Repeating Audio 1...");
          myDFPlayer.play(1);
          repeatPlayed = true;
        }

        // Status print every second
        if (millis() - lastWindowPrint > 1000) {
          Serial.print("   [Window] Time elapsed: ");
          Serial.print((millis() - wStart) / 1000);
          Serial.println("s | Waiting for sanitizer signal...");
          lastWindowPrint = millis();
        }

        // Check if Node 2 sent the sanitizer signal via ESP-NOW
        if (sanitizerSignalReceived) {
          break;
        }

        delay(100);
      }

      // -------- Step 3: Evaluate outcome --------
      if (sanitizerSignalReceived) {
        Serial.println(">>> COMPLIANCE SUCCESS.");
        client.publish("v1/devices/me/telemetry", "{\"status\":\"SANITIZED\",\"sanitized\":1}");
        myDFPlayer.play(2);
      } else {
        Serial.println(">>> COMPLIANCE VIOLATION! Triggering Camera.");
        client.publish("v1/devices/me/telemetry", "{\"status\":\"VIOLATION\",\"violation\":1}");
        myDFPlayer.play(3);
        uint8_t signal = 1;
        esp_now_send(camMAC, &signal, sizeof(signal));
      }

      delay(5000);
    }
  }
}
