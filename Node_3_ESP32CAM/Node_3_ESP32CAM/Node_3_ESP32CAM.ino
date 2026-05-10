#include <WiFi.h>
#include <WiFiClientSecure.h>
#include "esp_camera.h"
#include <esp_now.h>
#include <time.h>
#include <ESPmDNS.h>
#include <WebServer.h>

// ============================================================
//  CONFIGURATION — update these
// ============================================================

const char* ssid     = "Umesh";
const char* password = "Umesh123@";

const char* serverIP          = "10.65.211.238";
const int   serverPort        = 5000;
const char* violationEndpoint = "/violation";

const char* ntpServer          = "pool.ntp.org";
const long  gmtOffset_sec      = 19800;   // IST = UTC+5:30
const int   daylightOffset_sec = 0;

// ── Burst settings ──────────────────────────────────────────
#define BURST_COUNT     5
#define WARMUP_FRAMES   2
#define MIN_FRAME_BYTES 4000

// ============================================================

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

WiFiClient client;
WebServer  camServer(80);   // port 80 — serves /capture for register_staff.py

volatile bool takePhoto    = false;
volatile bool burstRunning = false;
bool          pendingBurst = false;

// ============================================================
//  /capture endpoint
//  Called by register_staff.py on the laptop:
//    GET http://esp32cam.local/capture
//  Returns a JPEG photo for staff enrollment.
//  Has nothing to do with violation detection.
// ============================================================
void handleCapture() {
  Serial.println("[WebServer] /capture — taking registration photo...");

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    camServer.send(500, "text/plain", "Capture failed");
    Serial.println("[WebServer] ✗ Capture failed");
    return;
  }

  camServer.sendHeader("Content-Disposition", "inline; filename=capture.jpg");
  camServer.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);

  Serial.println("[WebServer] ✓ Registration photo served");
}

void handlePing() {
  camServer.send(200, "text/plain", "esp32cam OK");
}

// ============================================================
//  ESP-NOW callback — violation signal from Node 1
// ============================================================
void OnDataRecv(const esp_now_recv_info_t *recv_info,
                const uint8_t *data, int len) {
  if (len > 0 && data[0] == 1) {
    if (burstRunning) {
      pendingBurst = true;
      Serial.println("[ESP-NOW] Signal queued (burst in progress)");
    } else {
      takePhoto = true;
      Serial.println("[ESP-NOW] ✓ Violation signal received");
    }
  }
}

// ============================================================
//  Ensure WiFi is connected
// ============================================================
bool ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return true;

  Serial.println("[WiFi] Reconnecting...");
  WiFi.disconnect();
  delay(300);
  WiFi.begin(ssid, password);

  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 10000) {
    delay(500); Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("[WiFi] ✓ Reconnected: " + WiFi.localIP().toString());
    return true;
  }
  Serial.println("[WiFi] ✗ Reconnect failed");
  return false;
}

// ============================================================
//  Send violation frame to Flask server
// ============================================================
void sendFrame(camera_fb_t* fb) {
  if (!ensureWiFi()) {
    Serial.println("[Send] ✗ No WiFi — frame dropped");
    return;
  }

  if (!client.connect(serverIP, serverPort)) {
    Serial.println("[Send] ✗ Connection failed");
    return;
  }

  String head = "--Boundary\r\n"
                "Content-Disposition: form-data; name=\"photo\"; filename=\"violation.jpg\"\r\n"
                "Content-Type: image/jpeg\r\n\r\n";
  String tail = "\r\n--Boundary--\r\n";
  long contentLength = head.length() + fb->len + tail.length();

  client.println("POST " + String(violationEndpoint) + " HTTP/1.1");
  client.println("Host: " + String(serverIP));
  client.println("Content-Length: " + String(contentLength));
  client.println("Content-Type: multipart/form-data; boundary=Boundary");
  client.println("Connection: close");
  client.println();
  client.print(head);

  uint8_t* buf = fb->buf;
  size_t   len = fb->len;
  for (size_t n = 0; n < len; n += 1024) {
    size_t chunk = (n + 1024 < len) ? 1024 : (len - n);
    client.write(buf + n, chunk);
  }
  client.print(tail);

  Serial.println("[Send] ✓ Frame uploaded (" + String(fb->len) + " bytes)");

  unsigned long t = millis();
  while (client.connected() && millis() - t < 4000) {
    if (client.available()) {
      String line = client.readStringUntil('\n');
      if (line.indexOf("200") != -1) {
        Serial.println("[Send] ✓ Server acknowledged");
        break;
      }
    }
    delay(50);
  }
  client.stop();
}

// ============================================================
//  Burst capture — picks sharpest frame and sends to Flask
// ============================================================
void captureAndSendBest() {
  burstRunning = true;

  Serial.println("\n" + String(60, '='));
  Serial.println("[BURST] Violation triggered — starting burst");

  // Warmup: discard first frames so auto-exposure settles
  Serial.println("[BURST] Warming up...");
  for (int i = 0; i < WARMUP_FRAMES; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) {
      Serial.println("[BURST]   warmup " + String(i+1) +
                     " discarded (" + String(fb->len) + " bytes)");
      esp_camera_fb_return(fb);
    }
    delay(120);
  }

  // Capture candidates
  int          candidateCount = BURST_COUNT - WARMUP_FRAMES;  // 3
  size_t       sizes[3];
  camera_fb_t* frames[3];

  for (int i = 0; i < candidateCount; i++) {
    delay(100);
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("[BURST]   frame " + String(i+1) + " — FAILED");
      frames[i] = nullptr; sizes[i] = 0;
      continue;
    }
    frames[i] = fb; sizes[i] = fb->len;
    Serial.println("[BURST]   frame " + String(i+1) +
                   " — " + String(fb->len) + " bytes");
  }

  // Pick largest (sharpest)
  int bestIdx = -1; size_t bestSize = MIN_FRAME_BYTES;
  for (int i = 0; i < candidateCount; i++) {
    if (frames[i] && sizes[i] > bestSize) {
      bestSize = sizes[i]; bestIdx = i;
    }
  }

  if (bestIdx == -1) {
    Serial.println("[BURST] ✗ No valid frame — skipping");
  } else {
    Serial.println("[BURST] ✓ Best: frame #" + String(bestIdx+1) +
                   " (" + String(bestSize) + " bytes)");
    sendFrame(frames[bestIdx]);
  }

  for (int i = 0; i < candidateCount; i++) {
    if (frames[i]) esp_camera_fb_return(frames[i]);
  }

  Serial.println("[BURST] Done.");
  Serial.println(String(60, '=') + "\n");
  burstRunning = false;
}

// ============================================================
//  Setup
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n" + String(60, '='));
  Serial.println("  NICU ESP32-CAM — BOOT");
  Serial.println(String(60, '='));

  // BOOT-1: Camera
  camera_config_t config;
  config.ledc_channel  = LEDC_CHANNEL_0; config.ledc_timer    = LEDC_TIMER_0;
  config.pin_d0 = 5;  config.pin_d1 = 18; config.pin_d2 = 19; config.pin_d3 = 21;
  config.pin_d4 = 36; config.pin_d5 = 39; config.pin_d6 = 34; config.pin_d7 = 35;
  config.pin_xclk = 0; config.pin_pclk = 22; config.pin_vsync = 25; config.pin_href = 23;
  config.pin_sscb_sda = 26; config.pin_sscb_scl = 27;
  config.pin_pwdn = 32; config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_VGA;
  config.jpeg_quality = 10;
  config.fb_count     = 2;

  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("[BOOT-1] ✗ Camera FAILED — halting");
    while (1) { delay(1000); Serial.print("X"); }
  }
  Serial.println("[BOOT-1] ✓ Camera ready");

  // BOOT-2: WiFi
  Serial.println("[BOOT-2] Connecting WiFi...");
  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500); Serial.print("."); attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[BOOT-2] ✓ WiFi: " + WiFi.localIP().toString());
  } else {
    Serial.println("\n[BOOT-2] ✗ WiFi FAILED — halting");
    while (1) { delay(1000); Serial.print("X"); }
  }

  // BOOT-3: mDNS — makes camera reachable as "esp32cam.local"
  // register_staff.py uses http://esp32cam.local/capture
  // No need to know or hardcode the IP address
  if (MDNS.begin("esp32cam")) {
    Serial.println("[BOOT-3] ✓ mDNS: http://esp32cam.local");
  } else {
    Serial.println("[BOOT-3] ✗ mDNS failed — use IP: " + WiFi.localIP().toString());
  }

  // BOOT-4: WebServer — serves /capture for register_staff.py
  camServer.on("/capture", HTTP_GET, handleCapture);
  camServer.on("/ping",    HTTP_GET, handlePing);
  camServer.begin();
  Serial.println("[BOOT-4] ✓ WebServer ready — http://esp32cam.local/capture");

  // BOOT-5: NTP
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  delay(2000);
  Serial.println("[BOOT-5] ✓ NTP synced");

  // BOOT-6: ESP-NOW
  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(OnDataRecv);
    Serial.println("[BOOT-6] ✓ ESP-NOW ready");
  } else {
    Serial.println("[BOOT-6] ✗ ESP-NOW failed");
  }

  Serial.println(String(60, '='));
  Serial.println("  BOOT COMPLETE");
  Serial.println("  Violation : waiting for ESP-NOW from Node 1");
  Serial.println("  Register  : http://esp32cam.local/capture");
  Serial.println(String(60, '=') + "\n");
}

// ============================================================
//  Loop
// ============================================================
void loop() {
  camServer.handleClient();   // serve /capture requests from register_staff.py

  if (takePhoto) {
    takePhoto = false;
    captureAndSendBest();

    if (pendingBurst) {
      pendingBurst = false;
      Serial.println("[Loop] Processing queued burst...");
      captureAndSendBest();
    }
  }

  delay(50);
}
