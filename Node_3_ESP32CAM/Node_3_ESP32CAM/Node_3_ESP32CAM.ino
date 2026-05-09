#include <WiFi.h>
#include <WiFiClientSecure.h>
#include "esp_camera.h"
#include <esp_now.h>
#include <time.h>

// -------- Configuration --------

const char* ssid = "Umesh";
const char* password = "Umesh123@";

// FLASK SERVER CONFIG
const char* serverIP = "10.65.211.238";
const int serverPort = 5000;
const char* violationEndpoint = "/violation";

// Telegram credentials
const char* BOTtoken = "8608535596:AAH3trNFwW1dlJIifgR2c6_VCV37R7B_Glw";
const char* CHAT_ID = "-1003714141923";

// Static IP Configuration
// IPAddress staticIP(192, 168, 1, 100);
// IPAddress gateway(192, 168, 1, 1);
// IPAddress subnet(255, 255, 255, 0);
// IPAddress dns(8, 8, 8, 8);

// NTP Server for Timestamps
const char* ntpServer = "pool.ntp.org";
const long  gmtOffset_sec = 19800;
const int   daylightOffset_sec = 0;

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

WiFiClient client;
volatile bool takePhoto = false;

// -------- Function to send system message --------
void sendSystemMessage(String message) {
  Serial.println("[SYSTEM] Sending status: " + message);
  if (client.connect(serverIP, serverPort)) {
    Serial.println("[SYSTEM] ✓ Connected to Flask server");
    String request = "POST " + String(violationEndpoint) + " HTTP/1.1\r\n";
    request += "Host: " + String(serverIP) + "\r\n";
    request += "Content-Type: application/json\r\n";
    request += "Content-Length: 20\r\n";
    request += "Connection: close\r\n\r\n";
    request += "{\"status\":\"online\"}";
    client.print(request);
    client.stop();
  } else {
    Serial.println("[SYSTEM] ✗ Failed to connect to Flask server");
  }
} // ← FIXED: closing brace was missing here

// -------- ESP-NOW Callback --------
void OnDataRecv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
  if (len > 0 && data[0] == 1) {
    takePhoto = true;
  }
}

// -------- Capture and Send Photo --------
void captureAndSend() {
  Serial.println("\n" + String(50, '='));
  Serial.println("[PHASE-1] 📸 VIOLATION ALERT - Capturing photo...");
  
  // Warm up camera
  for (int i = 0; i < 2; i++) {
    camera_fb_t * fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }

  // Capture frame
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[PHASE-1] ✗ CAMERA CAPTURE FAILED!");
    return;
  }
  
  Serial.println("[PHASE-1] ✓ Photo captured (" + String(fb->len) + " bytes)");

  // Get timestamp
  struct tm timeinfo;
  char timeStringBuff[50];
  if (!getLocalTime(&timeinfo)) {
    strcpy(timeStringBuff, "Time-Sync-Failed");
    Serial.println("[PHASE-2] ⚠️ NTP Time sync failed");
  } else {
    strftime(timeStringBuff, sizeof(timeStringBuff), "%Y-%m-%d %H:%M:%S", &timeinfo);
    Serial.println("[PHASE-2] ✓ Timestamp: " + String(timeStringBuff));
  }

  // Ensure WiFi connected
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[PHASE-3] 🌐 WiFi lost. Reconnecting...");
    WiFi.disconnect();
    delay(500);
    WiFi.begin(ssid, password);
    
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
      delay(500);
      Serial.print(".");
    }
    Serial.println();
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("[PHASE-3] ✓ WiFi connected. Connecting to Flask server...");
    
    if (client.connect(serverIP, serverPort)) {
      Serial.println("[PHASE-3] ✓ Connected to Flask server at " + String(serverIP) + ":" + String(serverPort));
      Serial.println("[PHASE-4] 📤 Uploading photo...");

      // Build multipart form data
      String head = "--Boundary\r\n"
                    "Content-Disposition: form-data; name=\"photo\"; filename=\"violation.jpg\"\r\n"
                    "Content-Type: image/jpeg\r\n\r\n";
      String tail = "\r\n--Boundary--\r\n";

      long contentLength = head.length() + fb->len + tail.length();

      // Send HTTP headers
      client.println("POST " + String(violationEndpoint) + " HTTP/1.1");
      client.println("Host: " + String(serverIP));
      client.println("Content-Length: " + String(contentLength));
      client.println("Content-Type: multipart/form-data; boundary=Boundary");
      client.println("Connection: close");
      client.println();
      
      // Send form header
      client.print(head);

      // Send photo binary data in chunks
      uint8_t *fbBuf = fb->buf;
      size_t fbLen = fb->len;
      size_t chunkSize = 1024;
      for (size_t n = 0; n < fbLen; n += chunkSize) {
        if (n + chunkSize < fbLen) {
          client.write(fbBuf + n, chunkSize);
        } else {
          client.write(fbBuf + n, fbLen - n);
        }
        Serial.print(".");
      }
      Serial.println();
      
      // Send form footer
      client.print(tail);
      
      Serial.println("[PHASE-4] ✓ Photo uploaded successfully!");

      // Wait for response
      Serial.println("[PHASE-5] ⏳ Waiting for server response...");
      unsigned long timeout = millis();
      while (client.connected() && millis() - timeout < 5000) {
        if (client.available()) {
          String response = client.readStringUntil('\n');
          if (response.indexOf("200 OK") != -1 || response.indexOf("received") != -1) {
            Serial.println("[PHASE-5] ✓ Server accepted photo!");
            break;
          }
        }
        delay(100);
      }
    } else {
      Serial.println("[PHASE-3] ✗ FAILED to connect to Flask server!");
      Serial.println("[PHASE-3] ✗ Check: Is Python app running? Correct IP? Firewall?");
    }
  } else {
    Serial.println("[PHASE-3] ✗ WiFi still not connected!");
  }
  
  esp_camera_fb_return(fb);
  client.stop();
  
  Serial.println("[COMPLETE] Ready for next violation detection");
  Serial.println(String(50, '=') + "\n");
}

// -------- Setup --------
void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\n\n" + String(50, '='));
  Serial.println("🚀 NICU ESP32-CAM BOOT SEQUENCE");
  Serial.println(String(50, '='));
  
  Serial.println("[BOOT-1] 📷 Initializing camera...");
  
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0; config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;  config.pin_d1 = 18; config.pin_d2 = 19; config.pin_d3 = 21;
  config.pin_d4 = 36; config.pin_d5 = 39; config.pin_d6 = 34; config.pin_d7 = 35;
  config.pin_xclk = 0;  config.pin_pclk = 22; config.pin_vsync = 25; config.pin_href = 23;
  config.pin_sscb_sda = 26; config.pin_sscb_scl = 27; config.pin_pwdn = 32; config.pin_reset = -1;
  config.xclk_freq_hz = 20000000; config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 15;
  config.fb_count = 1;
  
  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("[BOOT-1] ✗ CAMERA INITIALIZATION FAILED!");
    while(1) { delay(1000); Serial.print("X"); }
  }
  Serial.println("[BOOT-1] ✓ Camera initialized successfully!");

  Serial.println("[BOOT-2] 🌐 Connecting to WiFi: " + String(ssid) + "...");
  // WiFi.config(staticIP, gateway, subnet, dns);
  WiFi.begin(ssid, password);
  
  int wifiAttempts = 0;
  while (WiFi.status() != WL_CONNECTED && wifiAttempts < 30) {
    delay(500);
    Serial.print(".");
    wifiAttempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[BOOT-2] ✓ WiFi Connected!");
    Serial.println("[BOOT-2] ✓ IP: " + WiFi.localIP().toString());
  } else {
    Serial.println("\n[BOOT-2] ✗ WiFi CONNECTION FAILED!");
    while(1) { delay(1000); Serial.print("X"); }
  }

  Serial.println("[BOOT-3] 🕐 Syncing time via NTP...");
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  delay(2000);
  Serial.println("[BOOT-3] ✓ Time synchronized!");

  Serial.println("[BOOT-4] 🔔 Setting up ESP-NOW receiver...");
  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(OnDataRecv);
    Serial.println("[BOOT-4] ✓ ESP-NOW Ready!");
  } else {
    Serial.println("[BOOT-4] ✗ ESP-NOW failed!");
  }
  
  Serial.println(String(50, '='));
  Serial.println("✅ BOOT COMPLETE - System Ready!");
  Serial.println("⏳ Waiting for violation signal from Node_1...");
  Serial.println(String(50, '=') + "\n");
}

// -------- Loop --------
// void loop() {
//   if (true) {
//     for(int cnt = 0; i<5; i++){
//     captureAndSend();
//     }

//     takePhoto = false;
//   }
// }

// -------- Loop --------
// void loop() {

//   Serial.println("📸 TEST MODE: Taking 5 photos...");

//   for (int cnt = 0; cnt < 5; cnt++) {

//     Serial.println("\n======================");
//     Serial.println("PHOTO " + String(cnt + 1));
//     Serial.println("======================");

//     captureAndSend();

//     delay(5000);
//   }

//   Serial.println("✅ TEST COMPLETE");

//   while(true){
//     delay(1000);
//   }
// }

void loop() {
  while(true){
    captureAndSend();
    delay(2000);
  }
}