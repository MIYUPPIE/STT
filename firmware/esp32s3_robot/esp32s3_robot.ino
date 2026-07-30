// esp32s3_robot.ino — USB-serial motor controller for a 2-wheel (differential
// drive) robot on an ESP32-S3. The LAPTOP is the brain: the Yoruba voice pipeline
// (services/robot) sends one-line text commands over USB; this firmware drives a
// dual H-bridge (L298N / L9110 / TB6612 class) and ACKs every command so the
// laptop knows it landed.
//
// H-bridge wiring (as given):
//   IN1 -> GPIO 4   IN2 -> GPIO 5   : left  motor (A)
//   IN3 -> GPIO 6   IN4 -> GPIO 7   : right motor (B)
//   ENA/ENB jumpered HIGH (speed comes from PWM on the IN pins).
//   Motor supply to the driver's V+, GND common with the ESP32.
//
// Board setting (which USB port is your cable in?) -> Arduino IDE, Tools:
//   * UART / COM port (a CH340/CH343/CP210x bridge, VID 1A86 or 10C4)  -> the
//     common case  ->  set "USB CDC On Boot: DISABLED" so Serial = UART0, which
//     is what that bridge is wired to.
//   * Native "USB" port of the S3 itself                              -> set
//     "USB CDC On Boot: Enabled" so Serial = the native USB CDC.
// If Serial says nothing on the port you opened, this setting is the mismatch.
// Baud 115200.
//
// Protocol (newline-terminated, case-insensitive):
//   F[,speed[,ms]]   forward         L[,speed[,ms]]   spin left
//   B[,speed[,ms]]   backward        R[,speed[,ms]]   spin right
//   S                stop now        P                ping
//   speed 0-255 (default 200), ms = auto-stop after this long (default 900,
//   max 5000). Every move auto-stops; nothing latches forever.
// Replies: "OK:F:200:900" / "OK:S" / "PONG" / "ERR:<why>".
//
// Safety: a move always auto-stops after its ms window, and a watchdog stops the
// motors if no serial byte arrives for WATCHDOG_MS (a crashed/unplugged laptop
// can never leave the robot running).

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

unsigned long moveUntil = 0;     // 0 = stopped; else millis() deadline
unsigned long lastByte  = 0;     // last time any serial byte arrived
String line;

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

void handle(String s) {
  s.trim();
  if (s.length() == 0) return;
  char cmd = toupper(s[0]);

  if (cmd == 'P') { Serial.println("PONG"); return; }
  if (cmd == 'S') { stopMotors(); Serial.println("OK:S"); return; }

  if (cmd == 'F' || cmd == 'B' || cmd == 'L' || cmd == 'R') {
    int speed = DEF_SPEED;
    unsigned long ms = DEF_MS;
    int c1 = s.indexOf(',');
    if (c1 >= 0) {
      int c2 = s.indexOf(',', c1 + 1);
      speed = s.substring(c1 + 1, c2 < 0 ? s.length() : c2).toInt();
      if (c2 >= 0) ms = (unsigned long) s.substring(c2 + 1).toInt();
    }
    startMove(cmd, speed, ms);
    Serial.printf("OK:%c:%d:%lu\n", cmd, speed <= 0 ? DEF_SPEED : speed,
                  ms == 0 ? DEF_MS : ms);
    return;
  }
  Serial.println("ERR:unknown");
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
  Serial.println("READY");   // laptop waits for this after opening the port
}

void loop() {
  while (Serial.available()) {
    lastByte = millis();
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (line.length()) { handle(line); line = ""; }
    } else if (line.length() < 32) {
      line += ch;
    }
  }

  unsigned long now = millis();
  if (moveUntil && now >= moveUntil) stopMotors();          // move window elapsed
  if (moveUntil && now - lastByte > WATCHDOG_MS) {          // comms lost -> halt
    stopMotors();
    Serial.println("OK:S:watchdog");
  }
}
