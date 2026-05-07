

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include "esp_camera.h"
#include <esp_now.h>
#include <time.h> // Required for timestamps

// -------- Configuration --------

const char* ssid = "vivotg";
const char* password = "12345678";
const char* BOTtoken = "8608535596:AAH3trNFwW1dlJIifgR2c6_VCV37R7B_Glw"; 
const char* CHAT_ID = "-1003714141923   "; // Use your 10-digit ID here

// Static IP Configuration
IPAddress staticIP(192, 168, 1, 100);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress dns(8, 8, 8, 8);

// NTP Server for Timestamps
const char* ntpServer = "pool.ntp.org";
const long  gmtOffset_sec = 19800; // India Time: GMT +5:30 (5.5 * 3600)
const int   daylightOffset_sec = 0;

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

WiFiClientSecure client;
volatile bool takePhoto = false;

// Function to send a text message (used for IP notification)
void sendTelegramMessage(String message) {
  client.setInsecure();
  if (client.connect("api.telegram.org", 443)) {
    String url = "/bot" + String(BOTtoken) + "/sendMessage?chat_id=" + String(CHAT_ID) + "&text=" + message;
    client.println("GET " + url + " HTTP/1.1");
    client.println("Host: api.telegram.org");
    client.println("Connection: close");
    client.println();
    client.stop();
    Serial.println("[System] IP Alert Sent.");
  }
}

void OnDataRecv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
  if (len > 0 && data[0] == 1) {
    takePhoto = true;
  }
}

void captureAndSend() {
  // --- PHASE 1: IMMEDIATE CAPTURE ---
  Serial.println("\n[1] ALERT. Capturing photo to RAM...");
  
  for (int i = 0; i < 2; i++) {
    camera_fb_t * fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }

  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Capture Failed!");
    return;
  }

  // --- PHASE 2: TIMESTAMP GENERATION ---
  struct tm timeinfo;
  char timeStringBuff[50];
  if(!getLocalTime(&timeinfo)){
    strcpy(timeStringBuff, "Time Sync Failed");
  } else {
    strftime(timeStringBuff, sizeof(timeStringBuff), "%Y-%m-%d %H:%M:%S", &timeinfo);
  }
  String caption = "⚠️ VIOLATION DETECTED\n🕒 Time: " + String(timeStringBuff);

  // --- PHASE 3: NETWORK RESET & UPLOAD ---
  WiFi.disconnect();
  delay(500); 
  WiFi.begin(ssid, password);
  
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
    delay(500); Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    client.setInsecure();
    client.setTimeout(15000); 

    if (client.connect("api.telegram.org", 443)) {
      Serial.println("\n[3] Connected. Sending Photo with Timestamp...");

      String head = "--Boundary\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n" + String(CHAT_ID) + 
                    "\r\n--Boundary\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n" + caption +
                    "\r\n--Boundary\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"esp32-cam.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n";
      String tail = "\r\n--Boundary--\r\n";

      client.println("POST /bot" + String(BOTtoken) + "/sendPhoto HTTP/1.1");
      client.println("Host: api.telegram.org");
      client.println("Content-Length: " + String(head.length() + fb->len + tail.length()));
      client.println("Content-Type: multipart/form-data; boundary=Boundary");
      client.println();
      client.print(head);

      uint8_t *fbBuf = fb->buf;
      size_t fbLen = fb->len;
      for (size_t n = 0; n < fbLen; n = n + 1024) {
        if (n + 1024 < fbLen) client.write(fbBuf, 1024);
        else client.write(fbBuf, fbLen - n);
        fbBuf += 1024;
      }
      client.print(tail);

      unsigned long wait = millis();
      while (millis() - wait < 5000) {
        if (client.available()) {
          String response = client.readString();
          if (response.indexOf("\"ok\":true") != -1) {
            Serial.println("SUCCESS: Photo + Timestamp delivered.");
            break;
          }
        }
      }
    }
  }
  
  esp_camera_fb_return(fb);
  client.stop();
  Serial.println("[4] Ready.");
}

void setup() {
  Serial.begin(115200);
  
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0; config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5; config.pin_d1 = 18; config.pin_d2 = 19; config.pin_d3 = 21;
  config.pin_d4 = 36; config.pin_d5 = 39; config.pin_d6 = 34; config.pin_d7 = 35;
  config.pin_xclk = 0; config.pin_pclk = 22; config.pin_vsync = 25; config.pin_href = 23;
  config.pin_sscb_sda = 26; config.pin_sscb_scl = 27; config.pin_pwdn = 32; config.pin_reset = -1;
  config.xclk_freq_hz = 20000000; config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA; 
  config.jpeg_quality = 15; 
  config.fb_count = 1;
  
  if (esp_camera_init(&config) != ESP_OK) Serial.println("Camera Failed");

  // Set static IP
  WiFi.config(staticIP, gateway, subnet, dns);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWiFi Ready.");

  // Init Time (NTP)
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

  // Send Startup IP notification
  String startupMsg = "🚀 NICU Camera Online!\nIP: http://" + WiFi.localIP().toString();
  sendTelegramMessage(startupMsg);

  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(OnDataRecv);
    Serial.println("ESP-NOW Ready.");
  }
}

void loop() {
  if (takePhoto) {
    captureAndSend();
    takePhoto = false;
  }
}