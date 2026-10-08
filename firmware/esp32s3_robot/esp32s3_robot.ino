// esp32s3_robot.ino — WiFi + USB motor controller for a 2-wheel (differential
// drive) robot on an ESP32-S3. The LAPTOP is the brain: the Yoruba voice pipeline
// (services/robot) sends one-line text commands, this firmware drives a dual
// H-bridge (L298N / L9110 / TB6612 class) and ACKs every command so the laptop
// knows it landed.
//
// Two command channels, same protocol, both live at once:
//   * WiFi  : TCP server on port 3333, mDNS name "yoruba-robot.local". This is
//             the wireless path (no cable to the laptop). One client at a time;
//             a new connection replaces the old one (so a restarted laptop
//             reconnects instantly).
//   * USB   : Serial at 115200 (flashing, logs, and the old wired control).
// WiFi credentials live in secrets.h (git-ignored; copy secrets.h.example). The
// board only joins 2.4 GHz networks. On boot it prints its IP on Serial.
//
// H-bridge wiring (as given):
//   IN1 -> GPIO 4   IN2 -> GPIO 5   : left  motor (A)
//   IN3 -> GPIO 6   IN4 -> GPIO 7   : right motor (B)
//   ENA/ENB jumpered HIGH (speed comes from PWM on the IN pins).
//   Motor supply to the driver's V+, GND common with the ESP32.
//
// Board setting (which USB port is your cable in?) -> Arduino IDE, Tools:
//   * UART / COM port (a CH340/CH343/CP210x bridge, VID 1A86 or 10C4)  -> the
//     common case  ->  set "USB CDC On Boot: DISABLED" so Serial = UART0.
//   * Native "USB" port of the S3 itself  -> "USB CDC On Boot: Enabled".
// (Only matters for the USB channel / boot log; WiFi works either way.)
//
// Protocol (newline-terminated, case-insensitive):
//   F[,speed[,ms]]   forward         L[,speed[,ms]]   spin left
//   B[,speed[,ms]]   backward        R[,speed[,ms]]   spin right
//   S                stop now        P                ping
//   speed 0-255 (default 200), ms = auto-stop after this long (default 900,
//   max 5000). Every move auto-stops; nothing latches forever.
// Replies (to the channel the command came from):
//   "OK:F:200:900" / "OK:S" / "PONG" / "ERR:<why>".
// Async lines (not replies): "READY" on connect/boot, "OK:S:watchdog".
//
// Safety: a move always auto-stops after its ms window; a watchdog stops the
// motors if no command byte arrives on any channel for WATCHDOG_MS; dropping the
// WiFi client or losing WiFi stops the motors immediately.

#include <WiFi.h>
#include <ESPmDNS.h>
#include "secrets.h"

// ---- pins ----
#define IN1 4   // left  A
#define IN2 5
#define IN3 6   // right B
#define IN4 7

// ---- tuning ----
#define DEF_SPEED     200
#define DEF_MS        900
#define MAX_MS        5000
#define WATCHDOG_MS   2000
// L298N is a slow bipolar driver: PWM on its INPUT pins must stay low (~1 kHz).
// 20 kHz makes it weak/unresponsive. A faint 1 kHz hum at part speed is normal.
// (For a MOSFET driver like TB6612/DRV8833 you can raise this to 20000.)
#define PWM_FREQ      1000
#define PWM_RES       8       // 0..255

// ---- network ----
#define TCP_PORT      3333
#define MDNS_NAME     "yoruba-robot"     // -> yoruba-robot.local
#define WIFI_RETRY_MS 5000               // re-begin() if still down this long

WiFiServer server(TCP_PORT);
WiFiClient client;

unsigned long moveUntil = 0;     // 0 = stopped; else millis() deadline
unsigned long lastByte  = 0;     // last time any command byte arrived
unsigned long wifiTry   = 0;     // last WiFi.begin()/reconnect attempt
bool wifiWasUp = false;
bool mdnsUp = false;
String serLine, netLine;

// PWM both pins of one motor via LEDC. speed in -255..255 (sign = direction).
void driveMotor(int inA, int inB, int speed) {
  if (speed > 255) speed = 255;
  if (speed < -255) speed = -255;
  if (speed >= 0) { ledcWrite(inA, speed);  ledcWrite(inB, 0); }
  else            { ledcWrite(inA, 0);       ledcWrite(inB, -speed); }
}

void motors(int left, int right) {
  driveMotor(IN1, IN2, left);
  driveMotor(IN3, IN4, right);
}

void stopMotors() {
  motors(0, 0);
  moveUntil = 0;
}

// Start a timed move. dir: forward/back = both wheels same sign; turns = opposite
// signs (spin in place, the most predictable response for voice control).
void startMove(char cmd, int speed, unsigned long ms) {
  if (speed <= 0) speed = DEF_SPEED;
  if (speed > 255) speed = 255;
  if (ms == 0) ms = DEF_MS;
  if (ms > MAX_MS) ms = MAX_MS;

  switch (cmd) {
    case 'F': motors( speed,  speed); break;
    case 'B': motors(-speed, -speed); break;
    case 'L': motors(-speed,  speed); break;   // left wheel back, right fwd
    case 'R': motors( speed, -speed); break;   // right wheel back, left fwd
    default:  stopMotors(); return;
  }
  moveUntil = millis() + ms;
}

// Parse + execute one command line; the reply goes back on `out`.
void handle(String s, Print &out) {
  s.trim();
  if (s.length() == 0) return;
  char cmd = toupper(s[0]);

  if (cmd == 'P') { out.print("PONG\n"); return; }
  if (cmd == 'S') { stopMotors(); out.print("OK:S\n"); return; }

  if (cmd == 'F' || cmd == 'B' || cmd == 'L' || cmd == 'R') {
    int speed = DEF_SPEED;
    unsigned long ms = DEF_MS;
    int c1 = s.indexOf(',');
    if (c1 >= 0) {
      int c2 = s.indexOf(',', c1 + 1);
      speed = s.substring(c1 + 1, c2 < 0 ? s.length() : c2).toInt();
      if (c2 >= 0) ms = (unsigned long) s.substring(c2 + 1).toInt();
    }
    if (speed > 255) speed = 255;
    if (ms > MAX_MS) ms = MAX_MS;
    startMove(cmd, speed, ms);
    out.printf("OK:%c:%d:%lu\n", cmd, speed <= 0 ? DEF_SPEED : speed,
               ms == 0 ? DEF_MS : ms);
    return;
  }
  out.print("ERR:unknown\n");
}

// Feed bytes from one channel into its line buffer; run complete lines.
void pump(Stream &in, Print &out, String &line) {
  while (in.available()) {
    lastByte = millis();
    char ch = in.read();
    if (ch == '\n' || ch == '\r') {
      if (line.length()) { handle(line, out); line = ""; }
    } else if (line.length() < 32) {
      line += ch;
    }
  }
}

void startWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);            // no modem sleep: keeps command latency ~ms
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  wifiTry = millis();
  Serial.printf("WiFi: joining \"%s\"...\n", WIFI_SSID);
}

// Track WiFi up/down edges: announce the IP + start services when it comes up,
// stop the motors when it drops (the laptop can no longer reach us).
void serviceWifi() {
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiWasUp) {
    Serial.printf("WiFi CONNECTED  ip=%s  tcp=%d  host=%s.local  rssi=%d\n",
                  WiFi.localIP().toString().c_str(), TCP_PORT, MDNS_NAME,
                  WiFi.RSSI());
    if (!mdnsUp && MDNS.begin(MDNS_NAME)) {
      MDNS.addService("yoruba-robot", "tcp", TCP_PORT);
      mdnsUp = true;
    }
    server.begin();
    server.setNoDelay(true);
  } else if (!up && wifiWasUp) {
    Serial.println("WiFi LOST -> motors stopped, reconnecting");
    if (client) client.stop();
    stopMotors();
    wifiTry = millis();
  } else if (!up && millis() - wifiTry > WIFI_RETRY_MS) {
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiTry = millis();
  }
  wifiWasUp = up;
}

// Accept a new TCP client (it replaces any old one) and pump its bytes.
void serviceClient() {
  if (!wifiWasUp) return;
  WiFiClient incoming = server.accept();
  if (incoming) {
    if (client) {
      client.stop();
      stopMotors();                // old controller gone: never coast on it
    }
    client = incoming;
    client.setNoDelay(true);
    netLine = "";
    lastByte = millis();
    client.print("READY\n");
    Serial.printf("client connected: %s\n", client.remoteIP().toString().c_str());
  }
  if (client) {
    if (client.connected()) {
      pump(client, client, netLine);
    } else {
      client.stop();
      stopMotors();
      Serial.println("client disconnected -> motors stopped");
    }
  }
}

void setup() {
  Serial.begin(115200);
  int pins[] = {IN1, IN2, IN3, IN4};
  for (int p : pins) {
    ledcAttach(p, PWM_FREQ, PWM_RES);   // core 3.x: allocates an LEDC channel
    ledcWrite(p, 0);
  }
  stopMotors();
  lastByte = millis();
  delay(200);
  startWifi();
  Serial.println("READY");   // a USB-attached laptop waits for this
}

void loop() {
  serviceWifi();
  serviceClient();
  pump(Serial, Serial, serLine);

  unsigned long now = millis();
  if (moveUntil && now >= moveUntil) stopMotors();          // move window elapsed
  if (moveUntil && now - lastByte > WATCHDOG_MS) {          // comms lost -> halt
    stopMotors();
    Serial.print("OK:S:watchdog\n");
    if (client && client.connected()) client.print("OK:S:watchdog\n");
  }
  delay(1);                                                 // yield to WiFi stack
}
