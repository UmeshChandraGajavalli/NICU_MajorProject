#include <WiFi.h>
#include <WiFiClientSecure.h>
#include "esp_camera.h"
#include <esp_now.h>
#include <time.h>

// ============================================================
//  CONFIGURATION — update these
// ============================================================

const char* ssid     = "Umesh";
const char* password = "Umesh123@";

const char* serverIP          = "10.65.211.238";
const int   serverPort        = 5000;
const char* violationEndpoint = "/violation";

const char* ntpServer          = "pool.ntp.org";
const long  gmtOffset_sec      = 19800;
const int   daylightOffset_sec = 0;

// ── Burst settings ──────────────────────────────────────────
#define BURST_COUNT   5    // total frames captured per trigger
#define WARMUP_FRAMES 2    // frames discarded at the start (sensor settling)
#define MIN_FRAME_BYTES 4000  // frames smaller than this are corrupt/rejected

// ============================================================

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

WiFiClient client;

volatile bool takePhoto    = false;
volatile bool burstRunning = false;
bool          pendingBurst = false;

// ============================================================
//  ESP-NOW callback
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
    delay(500);
    Serial.print(".");
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
//  Send one frame to Flask server
// ============================================================
void sendFrame(camera_fb_t* fb) {
  if (!ensureWiFi()) {
    Serial.println("[Send] ✗ No WiFi — frame dropped");
    return;
  }

  Serial.println("[Send] Connecting to server...");

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
//  Burst capture — picks the sharpest frame and sends it
// ============================================================
void captureAndSendBest() {
  burstRunning = true;

  Serial.println("\n" + String(60, '='));
  Serial.println("[BURST] Violation triggered — starting burst");

  // Step 1: warmup — throw away the first WARMUP_FRAMES
  Serial.println("[BURST] Warming up camera...");
  for (int i = 0; i < WARMUP_FRAMES; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) {
      Serial.println("[BURST]   warmup frame " + String(i+1) +
                     " discarded (" + String(fb->len) + " bytes)");
      esp_camera_fb_return(fb);
    }
    delay(120);
  }

  // Step 2: capture candidate frames
  int candidateCount = BURST_COUNT - WARMUP_FRAMES;  // = 3
  size_t       sizes[3];
  camera_fb_t* frames[3];

  Serial.println("[BURST] Capturing " + String(candidateCount) + " candidates...");

  for (int i = 0; i < candidateCount; i++) {
    delay(100);
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("[BURST]   frame " + String(i+1) + " — FAILED");
      frames[i] = nullptr;
      sizes[i]  = 0;
      continue;
    }
    frames[i] = fb;
    sizes[i]  = fb->len;
    Serial.println("[BURST]   frame " + String(i+1) +
                   " — " + String(fb->len) + " bytes");
  }

  // Step 3: pick largest valid frame (biggest JPEG = most detail = sharpest)
  int    bestIdx  = -1;
  size_t bestSize = MIN_FRAME_BYTES;

  for (int i = 0; i < candidateCount; i++) {
    if (frames[i] && sizes[i] > bestSize) {
      bestSize = sizes[i];
      bestIdx  = i;
    }
  }

  if (bestIdx == -1) {
    Serial.println("[BURST] ✗ No valid frame found — skipping send");
  } else {
    Serial.println("[BURST] ✓ Best frame: #" + String(bestIdx+1) +
                   " (" + String(bestSize) + " bytes) — sending...");
    sendFrame(frames[bestIdx]);
  }

  // Step 4: free all frame buffers
  for (int i = 0; i < candidateCount; i++) {
    if (frames[i]) esp_camera_fb_return(frames[i]);
  }

  Serial.println("[BURST] Done. Waiting for next violation.");
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

  // Camera
  Serial.println("[BOOT-1] Initializing camera...");
  camera_config_t config;
  config.ledc_channel  = LEDC_CHANNEL_0;
  config.ledc_timer    = LEDC_TIMER_0;
  config.pin_d0        = 5;   config.pin_d1      = 18;
  config.pin_d2        = 19;  config.pin_d3      = 21;
  config.pin_d4        = 36;  config.pin_d5      = 39;
  config.pin_d6        = 34;  config.pin_d7      = 35;
  config.pin_xclk      = 0;   config.pin_pclk    = 22;
  config.pin_vsync     = 25;  config.pin_href    = 23;
  config.pin_sscb_sda  = 26;  config.pin_sscb_scl = 27;
  config.pin_pwdn      = 32;  config.pin_reset   = -1;
  config.xclk_freq_hz  = 20000000;
  config.pixel_format  = PIXFORMAT_JPEG;
  config.frame_size    = FRAMESIZE_VGA;  // 640x480
  config.jpeg_quality  = 10;            // 10 = better than 15 on ESP32
  config.fb_count      = 2;

  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("[BOOT-1] ✗ Camera init FAILED — halting");
    while (1) { delay(1000); Serial.print("X"); }
  }
  Serial.println("[BOOT-1] ✓ Camera ready");

  // WiFi
  Serial.println("[BOOT-2] Connecting to WiFi: " + String(ssid) + "...");
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

  // NTP
  Serial.println("[BOOT-3] Syncing NTP...");
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  delay(2000);
  Serial.println("[BOOT-3] ✓ Time synced");

  // ESP-NOW
  Serial.println("[BOOT-4] Initializing ESP-NOW...");
  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(OnDataRecv);
    Serial.println("[BOOT-4] ✓ ESP-NOW ready");
  } else {
    Serial.println("[BOOT-4] ✗ ESP-NOW failed");
  }

  Serial.println(String(60, '='));
  Serial.println("  BOOT COMPLETE — idle, waiting for violation signal");
  Serial.println(String(60, '=') + "\n");
}

// ============================================================
//  Loop — fully idle until violation signal arrives
// ============================================================
void loop() {
  if (takePhoto) {
    takePhoto = false;
    captureAndSendBest();

    // If a trigger arrived during the burst, fire another one immediately
    if (pendingBurst) {
      pendingBurst = false;
      Serial.println("[Loop] Processing queued burst...");
      captureAndSendBest();
    }
  }

  delay(50);  // yield to WiFi stack, burns almost no power
}