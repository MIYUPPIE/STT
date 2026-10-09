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
//   * UDP 3334 telemetry: send "SUB" and receive "T,seq,ms,dutyL,dutyR,minDuty"
//             at 20 Hz for 3 s (re-send SUB every second to keep it coming).
//   * OTA   : after one USB flash, re-flash over WiFi from the Arduino IDE
//             (Tools > Port > yoruba-robot at <ip>). Password: OTA_PASSWORD.
// WiFi credentials live in secrets.h (git-ignored; copy secrets.h.example). The
// board only joins 2.4 GHz networks. On boot it prints its IP on Serial.
//
// L298N wiring (ESP32-S3):
//   IN1 -> GPIO 4   IN2 -> GPIO 5   ENA -> GPIO 41 : left  motor (A)
//   IN3 -> GPIO 6   IN4 -> GPIO 7   ENB -> GPIO 42 : right motor (B)
//   REMOVE the ENA/ENB jumper caps on the L298N, then wire ENA/ENB to the GPIOs.
//   With the caps on, the enables are hard-wired HIGH and speed control is dead.
//   Motor supply to the L298N's +12V/VS, GND common with the ESP32 (required).
//   3.3 V logic is enough for the L298N inputs (V_IH min 2.3 V).
//
// Speed control: IN pins are plain HIGH/LOW direction lines; speed is LEDC PWM
// on ENA/ENB. Command speed 1..255 maps onto MIN_DUTY..255 so slow commands
// still turn the wheels; LEFT_TRIM/RIGHT_TRIM make mismatched motors drive
// straight; starts and direction changes ramp (RAMP_MS) so a reversal never
// slams the bridge (that current spike can brown out the ESP32). Stops are
// always instant.
//
// Pin choice (ESP32-S3): 41/42 are free on Freenove/DevKitC-1 S3 boards: not
// strapping (0/3/45/46), not USB (19/20), not UART0 (43/44), not octal PSRAM
// (35-37), not the camera bus or the onboard LEDs (2/21/47).
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
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include "secrets.h"

#include "motor_math.h"

// ---- pins ----
#define IN1 4    // left  A direction
#define IN2 5
#define ENA 41   // left  A speed (PWM)
#define IN3 6    // right B direction
#define IN4 7
#define ENB 42   // right B speed (PWM)

// ---- tuning ----
#define DEF_SPEED     200
#define DEF_MS        900
#define MAX_MS        5000
#define WATCHDOG_MS   2000
// L298N is a slow bipolar driver: keep enable PWM low (~1 kHz). 20 kHz makes it
// weak/unresponsive. A faint 1 kHz hum at part speed is normal.
// (For a MOSFET driver like TB6612/DRV8833 you can raise this to 20000.)
#define PWM_FREQ      1000
#define PWM_RES       8       // 0..255
#define MIN_DUTY      90      // lowest duty that still turns the wheels (tune:
                              // raise if slow commands only hum, lower if
                              // slow is too fast)
#define LEFT_TRIM     100     // % (50..100): lower the faster wheel if the
#define RIGHT_TRIM    100     // robot curves when told to go straight
#define RAMP_MS       150     // 0 -> full speed time; 0 = no ramp
#define RAMP_TICK_MS  2

// ---- network ----
#define TCP_PORT      3333
#define MDNS_NAME     "yoruba-robot"     // -> yoruba-robot.local
#define WIFI_RETRY_MS 5000               // re-begin() if still down this long
// Telemetry: any laptop sends "SUB" to UDP TELEM_PORT; for TELEM_TTL_MS after
// each SUB the robot streams its actual motor duties to it at 1000/TELEM_MS Hz.
// Works no matter who is commanding (WiFi client, USB serial, watchdog stop),
// so RViz always shows what the motors are really doing.
#define TELEM_PORT    3334
#define TELEM_MS      50                 // 20 Hz
#define TELEM_SUBS    4
#define TELEM_TTL_MS  3000

WiFiServer server(TCP_PORT);
WiFiClient client;
WiFiUDP telem;

struct Subscriber { IPAddress ip; uint16_t port; unsigned long seen; };
Subscriber subs[TELEM_SUBS];
unsigned long telemSeq = 0, lastTelem = 0;
bool netUp = false;                      // UDP + OTA started on this WiFi session

unsigned long moveUntil = 0;     // 0 = stopped; else millis() deadline
unsigned long lastByte  = 0;     // last time any command byte arrived
unsigned long wifiTry   = 0;     // last WiFi.begin()/reconnect attempt
bool wifiWasUp = false;
bool mdnsUp = false;
String serLine, netLine;

struct Motor {
  int inA, inB, en, trim;
  int cur;      // signed duty actually applied now
  int target;   // signed duty we are ramping toward
};
Motor motorL = {IN1, IN2, ENA, LEFT_TRIM,  0, 0};
Motor motorR = {IN3, IN4, ENB, RIGHT_TRIM, 0, 0};
const int RAMP_STEP = motor::stepFor(RAMP_MS, RAMP_TICK_MS);
unsigned long lastRamp = 0;

// Drive one motor at signed duty: IN pins set direction, ENx PWM sets speed.
// duty 0 = coast (both INs LOW, enable off).
void applyMotor(Motor &m, int duty) {
  duty = motor::clampi(duty, -255, 255);
  if (duty == 0) {
    ledcWrite(m.en, 0);
    digitalWrite(m.inA, LOW);
    digitalWrite(m.inB, LOW);
  } else {
    if (duty > 0) { digitalWrite(m.inB, LOW);  digitalWrite(m.inA, HIGH); }
    else          { digitalWrite(m.inA, LOW);  digitalWrite(m.inB, HIGH); }
    ledcWrite(m.en, duty > 0 ? duty : -duty);
  }
  m.cur = duty;
}

// Set where each wheel should get to (sign = direction, 0..255 command speed);
// the ramp in loop() moves there smoothly.
void motors(int leftSpeed, int rightSpeed) {
  int l = motor::applyTrim(motor::speedToDuty(abs(leftSpeed), MIN_DUTY), motorL.trim);
  int r = motor::applyTrim(motor::speedToDuty(abs(rightSpeed), MIN_DUTY), motorR.trim);
  motorL.target  = leftSpeed  < 0 ? -l : l;
  motorR.target = rightSpeed < 0 ? -r : r;
}

// Instant stop (no ramp): dúró, watchdog, timeouts and link loss all land here.
void stopMotors() {
  motorL.target = motorR.target = 0;
  applyMotor(motorL, 0);
  applyMotor(motorR, 0);
  moveUntil = 0;
}

void rampMotors() {
  unsigned long now = millis();
  if (now - lastRamp < RAMP_TICK_MS) return;
  lastRamp = now;
  if (motorL.cur != motorL.target)
    applyMotor(motorL, motor::rampStep(motorL.cur, motorL.target, RAMP_STEP, MIN_DUTY));
  if (motorR.cur != motorR.target)
    applyMotor(motorR, motor::rampStep(motorR.cur, motorR.target, RAMP_STEP, MIN_DUTY));
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
    if (!netUp) {
      // ArduinoOTA starts mDNS with our hostname, so the board shows up in the
      // Arduino IDE as a network port and still resolves as yoruba-robot.local.
      ArduinoOTA.setHostname(MDNS_NAME);
#ifdef OTA_PASSWORD
      ArduinoOTA.setPassword(OTA_PASSWORD);
#endif
      ArduinoOTA.onStart([]() { stopMotors(); Serial.println("OTA update: motors stopped"); });
      ArduinoOTA.begin();
      if (!mdnsUp) {
        MDNS.addService("yoruba-robot", "tcp", TCP_PORT);
        mdnsUp = true;
      }
      telem.begin(TELEM_PORT);
      netUp = true;
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

// Remember (or refresh) a telemetry subscriber; the oldest slot is reused.
void addSubscriber(IPAddress ip, uint16_t port) {
  unsigned long now = millis();
  int slot = 0;
  for (int i = 0; i < TELEM_SUBS; i++) {
    if (subs[i].port == port && subs[i].ip == ip) { slot = i; break; }
    if (subs[i].seen < subs[slot].seen) slot = i;
  }
  subs[slot] = {ip, port, now};
}

// Read SUB requests; every TELEM_MS send the applied motor duties to each live
// subscriber.
void serviceTelemetry() {
  if (!wifiWasUp || !netUp) return;
  int size;
  while ((size = telem.parsePacket()) > 0) {
    char buf[8] = {0};
    telem.read(buf, sizeof(buf) - 1);
    if (buf[0] == 'S' && buf[1] == 'U' && buf[2] == 'B')
      addSubscriber(telem.remoteIP(), telem.remotePort());
  }
  unsigned long now = millis();
  if (now - lastTelem < TELEM_MS) return;
  lastTelem = now;
  char line[64];
  int len = motor::formatTelemetry(line, sizeof line, telemSeq++, now,
                                   motorL.cur, motorR.cur, MIN_DUTY);
  if (!len) return;
  for (int i = 0; i < TELEM_SUBS; i++) {
    if (subs[i].port == 0 || now - subs[i].seen > TELEM_TTL_MS) continue;
    telem.beginPacket(subs[i].ip, subs[i].port);
    telem.write((const uint8_t *)line, len);
    telem.endPacket();
  }
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
  int dirPins[] = {IN1, IN2, IN3, IN4};
  for (int p : dirPins) {
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }
  ledcAttach(ENA, PWM_FREQ, PWM_RES);   // core 3.x: allocates an LEDC channel
  ledcAttach(ENB, PWM_FREQ, PWM_RES);
  stopMotors();
  lastByte = millis();
  delay(200);
  startWifi();
  Serial.println("READY");   // a USB-attached laptop waits for this
}

void loop() {
  serviceWifi();
  if (netUp && wifiWasUp) ArduinoOTA.handle();
  serviceClient();
  pump(Serial, Serial, serLine);
  rampMotors();
  serviceTelemetry();

  unsigned long now = millis();
  if (moveUntil && now >= moveUntil) stopMotors();          // move window elapsed
  if (moveUntil && now - lastByte > WATCHDOG_MS) {          // comms lost -> halt
    stopMotors();
    Serial.print("OK:S:watchdog\n");
    if (client && client.connected()) client.print("OK:S:watchdog\n");
  }
  delay(1);                                                 // yield to WiFi stack
}
