// motor_math.h — pure speed math for esp32s3_robot.ino (no Arduino deps, so it
// is unit-tested on the host: firmware/esp32s3_robot/tests/test_motor_math.cpp).
//
// Duty values are signed: sign = direction (IN pins), magnitude 0..255 = PWM on
// the ENA/ENB enable pin.
#pragma once

namespace motor {

inline int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

// Command speed 0..255 -> enable-pin duty. 0 stays 0 (off). 1..255 maps
// linearly onto minDuty..255 so even the slowest command overcomes the motor's
// stall point (an L298N drops ~2 V, small gear motors don't turn below it).
inline int speedToDuty(int speed, int minDuty) {
  if (speed <= 0) return 0;
  speed = clampi(speed, 1, 255);
  minDuty = clampi(minDuty, 0, 255);
  return minDuty + (speed - 1) * (255 - minDuty) / 254;
}

// Per-wheel trim (percent, 50..100) to make a robot with mismatched motors drive
// straight. Never pushes a running wheel below 1.
inline int applyTrim(int duty, int trimPct) {
  if (duty <= 0) return 0;
  int d = duty * clampi(trimPct, 50, 100) / 100;
  return d < 1 ? 1 : d;
}

// One acceleration step from `cur` toward `target` (signed duties). A direction
// change always passes through 0 first (never flips the H-bridge at speed: that
// current spike browns out the ESP32). step <= 0 means jump straight there.
// deadZone: duties with 0 < |d| < deadZone can't turn the wheels, so a start
// jumps straight to deadZone and a slow-down to 0 cuts out below it (only when
// the target itself isn't inside the zone).
inline int rampRaw(int cur, int target, int step) {
  if (step <= 0 || cur == target) return target;
  if (cur > 0) {
    if (target > cur) return cur + step < target ? cur + step : target;
    int floor = target < 0 ? 0 : target;           // stop at 0 before reversing
    return cur - step > floor ? cur - step : floor;
  }
  if (cur < 0) {
    if (target < cur) return cur - step > target ? cur - step : target;
    int ceil = target > 0 ? 0 : target;
    return cur + step < ceil ? cur + step : ceil;
  }
  // cur == 0: start moving toward target
  if (target > 0) return step < target ? step : target;
  return -step > target ? -step : target;
}

inline int rampStep(int cur, int target, int step, int deadZone = 0) {
  int next = rampRaw(cur, target, step);
  int mag = next < 0 ? -next : next;
  int tmag = target < 0 ? -target : target;
  if (next == target || mag == 0 || mag >= deadZone) return next;
  if (cur == 0) {                                   // starting: skip the zone
    int jump = deadZone < tmag ? deadZone : tmag;
    return next > 0 ? jump : -jump;
  }
  bool towardZero = (cur > 0 && next < cur) || (cur < 0 && next > cur);
  bool slowInZone = target != 0 && (target > 0) == (cur > 0);  // e.g. 150 -> 50
  return towardZero && !slowInZone ? 0 : next;
}

// Ramp step size per tick so 0 -> 255 takes ~rampMs.
inline int stepFor(int rampMs, int tickMs) {
  if (rampMs <= 0) return 0;                        // 0 = no ramp (instant)
  int s = 255 * tickMs / rampMs;
  return s < 1 ? 1 : s;
}

}  // namespace motor
