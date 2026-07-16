// esp32s3_servo.ino — ESP32-S3 OV5640 camera + LED control + servo, driven over
// HTTP. The Yoruba voice pipeline (services/servo) POSTs absolute servo angles
// to /servo; the board is a pure function angle -> PWM pulse and holds no
// command logic of its own.
//
// Wiring for the servo (SG90 / MG996R class, 50 Hz PWM):
//   signal -> GPIO 14   (free pin; not used by the camera bus or the LEDs)
//   V+     -> external 5 V supply (NOT the ESP32 3V3 — a servo browns out the board)
//   GND    -> common ground shared with the ESP32
//
// LEDC allocation (must not collide):
//   camera XCLK : LEDC_TIMER_0 / LEDC_CHANNEL_0  @ 10 MHz  (set below + by esp_camera)
//   servo PWM   : LEDC_TIMER_1 / LEDC_CHANNEL_2  @ 50 Hz, 16-bit
//
// mDNS: the board answers to http://esp32-servo.local so the pipeline finds it
// without hard-coding the DHCP IP.

#include "esp_camera.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include "esp_http_server.h"
#include "driver/ledc.h"

#if ARDUINO_USB_CDC_ON_BOOT
  #include "USB.h"
#endif

// ================== WiFi ==================
const char *ssid = "Newage";
const char *password = "newage58990";

// ================== LED Pins ==================
#define LED_2   2
#define LED_21  21
#define LED_47  47

// ================== Servo ==================
#define SERVO_GPIO        14                 // free pin, safe on ESP32-S3
#define SERVO_TIMER       LEDC_TIMER_1       // NOT timer 0 (camera XCLK)
#define SERVO_CHANNEL     LEDC_CHANNEL_2     // NOT channel 0 (camera XCLK)
#define SERVO_FREQ_HZ     50                 // standard hobby servo frame rate
#define SERVO_RES         LEDC_TIMER_16_BIT  // 65536 duty steps -> smooth motion
#define SERVO_PERIOD_US   20000              // 1 / 50 Hz
#define SERVO_MIN_US      500                // pulse at 0 deg
#define SERVO_MAX_US      2500               // pulse at 180 deg
#define SERVO_MIN_DEG     0
#define SERVO_MAX_DEG     180

int servoAngle = 90;                         // last commanded angle (board state)

// ================== Camera Pins ==================
#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     15
#define SIOD_GPIO_NUM     4
#define SIOC_GPIO_NUM     5

#define Y2_GPIO_NUM       11
#define Y3_GPIO_NUM       9
#define Y4_GPIO_NUM       8
#define Y5_GPIO_NUM       10
#define Y6_GPIO_NUM       12
#define Y7_GPIO_NUM       18
#define Y8_GPIO_NUM       17
#define Y9_GPIO_NUM       16

#define VSYNC_GPIO_NUM    6
#define HREF_GPIO_NUM     7
#define PCLK_GPIO_NUM     13

httpd_handle_t camera_httpd = NULL;

bool ledState[3] = {false, false, false};
bool cameraOK = false;

// ================== Servo helpers ==================
void servoInit() {
  ledc_timer_config_t servo_timer = {
    .speed_mode      = LEDC_LOW_SPEED_MODE,
    .duty_resolution = SERVO_RES,
    .timer_num       = SERVO_TIMER,
    .freq_hz         = SERVO_FREQ_HZ,
    .clk_cfg         = LEDC_AUTO_CLK
  };
  ledc_timer_config(&servo_timer);

  ledc_channel_config_t servo_ch = {
    .gpio_num   = SERVO_GPIO,
    .speed_mode = LEDC_LOW_SPEED_MODE,
    .channel    = SERVO_CHANNEL,
    .timer_sel  = SERVO_TIMER,
    .duty       = 0,
    .hpoint     = 0
  };
  ledc_channel_config(&servo_ch);
}

// Absolute angle -> pulse width -> 16-bit duty. Clamps to the servo's range so a
// bad command can never drive the horn past its mechanical stops.
void setServoAngle(int deg) {
  if (deg < SERVO_MIN_DEG) deg = SERVO_MIN_DEG;
  if (deg > SERVO_MAX_DEG) deg = SERVO_MAX_DEG;
  servoAngle = deg;

  uint32_t pulse_us = SERVO_MIN_US +
                      (uint32_t)(deg) * (SERVO_MAX_US - SERVO_MIN_US) / SERVO_MAX_DEG;
  uint32_t max_duty = (1u << 16);            // 16-bit resolution
  uint32_t duty     = (uint64_t)pulse_us * max_duty / SERVO_PERIOD_US;

  ledc_set_duty(LEDC_LOW_SPEED_MODE, SERVO_CHANNEL, duty);
  ledc_update_duty(LEDC_LOW_SPEED_MODE, SERVO_CHANNEL);
}

// ================== HTML ==================
static const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <title>ESP32-S3 Camera + LEDs + Servo</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; }
    body { text-align:center; font-family:'Segoe UI',Arial,sans-serif;
           background:#121212; color:#e0e0e0; margin:0; padding:20px; }
    h2 { color:#2196F3; margin-bottom:5px; }
    .ip { color:#888; font-size:14px; margin-bottom:20px; }
    .stream-container { max-width:640px; margin:0 auto 25px auto;
      border:3px solid #2196F3; border-radius:10px; overflow:hidden; background:#000; }
    .stream-container img { width:100%; display:block; }
    .panel { max-width:640px; margin:0 auto 25px auto; background:#1e1e1e;
      border:2px solid #333; border-radius:12px; padding:20px; }
    .led-grid { display:flex; justify-content:center; gap:20px; flex-wrap:wrap; }
    .led-card { background:#1e1e1e; border-radius:12px; padding:20px; width:160px;
      border:2px solid #333; transition:border-color .3s; }
    .led-card.active { border-color:#4CAF50; }
    .led-card.inactive { border-color:#f44336; }
    .led-label { font-size:18px; font-weight:bold; margin-bottom:10px; }
    .led-status { font-size:13px; color:#888; margin-bottom:15px; }
    .btn { width:100%; padding:10px; border:none; border-radius:6px; font-size:14px;
      font-weight:bold; cursor:pointer; color:#fff; transition:opacity .2s; }
    .btn:hover { opacity:.85; }
    .btn-on { background:#4CAF50; }
    .btn-off { background:#f44336; }
    .led-indicator { width:20px; height:20px; border-radius:50%; margin:0 auto 10px auto;
      background:#333; transition:all .3s; }
    .led-indicator.on { background:#4CAF50; box-shadow:0 0 12px #4CAF50; }
    .led-indicator.off { background:#f44336; box-shadow:0 0 12px #f44336; }
    input[type=range] { width:100%; }
    .angle { font-size:28px; font-weight:bold; color:#2196F3; }
  </style>
</head>
<body>
  <h2>ESP32-S3 OV5640 Stream</h2>
  <div class="ip" id="ipAddr">Loading...</div>

  <div class="stream-container">
    <img src="/stream" id="stream">
  </div>

  <div class="panel">
    <div class="led-label">Servo (GPIO 14)</div>
    <div class="angle" id="angleVal">90&deg;</div>
    <input type="range" min="0" max="180" value="90" id="servo"
           oninput="setServo(this.value)">
  </div>

  <h3>LED Control</h3>
  <div class="led-grid">
    <div class="led-card" id="card2">
      <div class="led-indicator off" id="ind2"></div>
      <div class="led-label">LED GPIO 2</div>
      <div class="led-status" id="status2">OFF</div>
      <button class="btn btn-on" onclick="setLed(2,1)">ON</button>
      <button class="btn btn-off" onclick="setLed(2,0)" style="margin-top:8px;">OFF</button>
    </div>
    <div class="led-card" id="card21">
      <div class="led-indicator off" id="ind21"></div>
      <div class="led-label">LED GPIO 21</div>
      <div class="led-status" id="status21">OFF</div>
      <button class="btn btn-on" onclick="setLed(21,1)">ON</button>
      <button class="btn btn-off" onclick="setLed(21,0)" style="margin-top:8px;">OFF</button>
    </div>
    <div class="led-card" id="card47">
      <div class="led-indicator off" id="ind47"></div>
      <div class="led-label">LED GPIO 47</div>
      <div class="led-status" id="status47">OFF</div>
      <button class="btn btn-on" onclick="setLed(47,1)">ON</button>
      <button class="btn btn-off" onclick="setLed(47,0)" style="margin-top:8px;">OFF</button>
    </div>
  </div>

  <script>
    function setServo(v) {
      document.getElementById('angleVal').innerHTML = v + '&deg;';
      fetch('/servo?angle=' + v).catch(e => console.error(e));
    }
    function setLed(pin, state) {
      fetch('/led?pin=' + pin + '&state=' + state)
        .then(r => r.text())
        .then(txt => { if (txt.startsWith('OK')) updateUI(pin, state); })
        .catch(e => console.error(e));
    }
    function updateUI(pin, state) {
      var ind = document.getElementById('ind' + pin);
      var card = document.getElementById('card' + pin);
      var status = document.getElementById('status' + pin);
      if (state == 1) { ind.className='led-indicator on'; card.className='led-card active';
        status.innerText='ON'; status.style.color='#4CAF50'; }
      else { ind.className='led-indicator off'; card.className='led-card inactive';
        status.innerText='OFF'; status.style.color='#f44336'; }
    }
    document.getElementById('ipAddr').innerText = 'IP: ' + window.location.host;
  </script>
</body>
</html>
)rawliteral";

// ================== HANDLERS ==================
static esp_err_t index_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, INDEX_HTML, strlen(INDEX_HTML));
}

// /servo?angle=N  -> move to absolute angle N (0..180), reply "OK: angle=N".
// /servo          -> no move; reply "OK: angle=<current>" (used as a health ping).
static esp_err_t servo_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/plain");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  char angleStr[8];
  bool haveAngle = false;
  int angle = servoAngle;

  size_t query_len = httpd_req_get_url_query_len(req) + 1;
  if (query_len > 1) {
    char *query = (char *)malloc(query_len);
    if (query && httpd_req_get_url_query_str(req, query, query_len) == ESP_OK) {
      if (httpd_query_key_value(query, "angle", angleStr, sizeof(angleStr)) == ESP_OK) {
        angle = atoi(angleStr);
        haveAngle = true;
      }
    }
    free(query);
  }

  if (haveAngle) setServoAngle(angle);       // clamps internally

  char resp[32];
  snprintf(resp, sizeof(resp), "OK: angle=%d", servoAngle);
  return httpd_resp_send(req, resp, strlen(resp));
}

static esp_err_t led_handler(httpd_req_t *req) {
  char pinStr[8], stateStr[8];
  int pin = -1, state = -1;

  size_t query_len = httpd_req_get_url_query_len(req) + 1;
  if (query_len > 1) {
    char *query = (char *)malloc(query_len);
    if (query && httpd_req_get_url_query_str(req, query, query_len) == ESP_OK) {
      if (httpd_query_key_value(query, "pin", pinStr, sizeof(pinStr)) == ESP_OK)
        pin = atoi(pinStr);
      if (httpd_query_key_value(query, "state", stateStr, sizeof(stateStr)) == ESP_OK)
        state = atoi(stateStr);
    }
    free(query);
  }

  int gpio = -1, idx = -1;
  if (pin == LED_2)       { gpio = LED_2;  idx = 0; }
  else if (pin == LED_21) { gpio = LED_21; idx = 1; }
  else if (pin == LED_47) { gpio = LED_47; idx = 2; }

  httpd_resp_set_type(req, "text/plain");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  if (gpio != -1 && (state == 0 || state == 1)) {
    digitalWrite(gpio, state ? HIGH : LOW);
    ledState[idx] = state ? true : false;
    char resp[64];
    snprintf(resp, sizeof(resp), "OK: GPIO %d = %s", gpio, state ? "ON" : "OFF");
    return httpd_resp_send(req, resp, strlen(resp));
  }
  return httpd_resp_send(req, "ERROR: Invalid pin or state", 27);
}

static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t *fb = NULL;
  esp_err_t res = ESP_OK;

  httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=frame");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) { delay(200); continue; }

    res = httpd_resp_send_chunk(req, "--frame\r\n", strlen("--frame\r\n"));
    if (res == ESP_OK) {
      char header[128];
      int hlen = snprintf(header, sizeof(header),
                          "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
      res = httpd_resp_send_chunk(req, header, hlen);
    }
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, "\r\n", 2);

    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;
    delay(100);
  }
  return res;
}

// ================== SERVER ==================
void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.stack_size = 16384;
  config.max_uri_handlers = 8;

  httpd_uri_t index_uri  = { .uri = "/",       .method = HTTP_GET, .handler = index_handler,  .user_ctx = NULL };
  httpd_uri_t stream_uri = { .uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = NULL };
  httpd_uri_t led_uri    = { .uri = "/led",    .method = HTTP_GET, .handler = led_handler,    .user_ctx = NULL };
  httpd_uri_t servo_uri  = { .uri = "/servo",  .method = HTTP_GET, .handler = servo_handler,  .user_ctx = NULL };

  if (httpd_start(&camera_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(camera_httpd, &index_uri);
    httpd_register_uri_handler(camera_httpd, &stream_uri);
    httpd_register_uri_handler(camera_httpd, &led_uri);
    httpd_register_uri_handler(camera_httpd, &servo_uri);
    Serial.println("HTTP server started on port 80");
  } else {
    Serial.println("HTTP server FAILED to start!");
  }
}

// ================== SETUP ==================
void setup() {
  Serial.begin(115200);
  #if ARDUINO_USB_CDC_ON_BOOT
    USB.begin();
  #endif
  delay(3000);

  Serial.println("\n=== ESP32-S3 Camera + LED + Servo ===");

  // ---- LEDC for camera XCLK (timer 0 / channel 0) ----
  ledc_timer_config_t ledc_timer = {
    .speed_mode      = LEDC_LOW_SPEED_MODE,
    .duty_resolution = LEDC_TIMER_1_BIT,
    .timer_num       = LEDC_TIMER_0,
    .freq_hz         = 10000000,
    .clk_cfg         = LEDC_AUTO_CLK
  };
  ledc_timer_config(&ledc_timer);
  ledc_channel_config_t ledc_channel = {
    .gpio_num   = XCLK_GPIO_NUM,
    .speed_mode = LEDC_LOW_SPEED_MODE,
    .channel    = LEDC_CHANNEL_0,
    .timer_sel  = LEDC_TIMER_0,
    .duty       = 1,
    .hpoint     = 0
  };
  ledc_channel_config(&ledc_channel);

  // ---- Servo (timer 1 / channel 2) ----
  servoInit();
  setServoAngle(90);                          // start centered
  Serial.println("Servo ready on GPIO 14 (center 90)");

  // ---- LEDs ----
  pinMode(LED_2, OUTPUT); pinMode(LED_21, OUTPUT); pinMode(LED_47, OUTPUT);
  digitalWrite(LED_2, LOW); digitalWrite(LED_21, LOW); digitalWrite(LED_47, LOW);

  // ---- Camera ----
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM; config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM; config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM; config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM; config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM; config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 20;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;

  esp_err_t camErr = esp_camera_init(&config);
  if (camErr != ESP_OK) {
    Serial.printf("Camera init FAILED (0x%x) - continuing without camera\n", camErr);
    cameraOK = false;
  } else {
    cameraOK = true;
    Serial.println("Camera init OK");
    sensor_t *s = esp_camera_sensor_get();
    if (s) { s->set_brightness(s, 0); s->set_contrast(s, 0); s->set_saturation(s, 0); }
  }

  // ---- WiFi ----
  Serial.printf("\nConnecting to: %s\n", ssid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
    if (++attempts > 60) { Serial.println("\nWiFi TIMEOUT!"); return; }
  }
  Serial.println("\nWiFi CONNECTED!");
  Serial.print("Open browser: http://");
  Serial.println(WiFi.localIP());

  // ---- mDNS so the pipeline reaches http://esp32-servo.local ----
  if (MDNS.begin("esp32-servo")) {
    MDNS.addService("http", "tcp", 80);
    Serial.println("mDNS: http://esp32-servo.local");
  } else {
    Serial.println("mDNS start failed");
  }

  startCameraServer();
}

// ================== LOOP ==================
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected! Reconnecting...");
    WiFi.reconnect();
  }
  delay(10000);
}
