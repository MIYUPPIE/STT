// test_motor_math.cpp — host gate test for motor_math.h (g++, no Arduino).
// Run: g++ -std=c++17 -Wall -Wextra -Werror -o /tmp/t tests/test_motor_math.cpp && /tmp/t
// (services/robot/tests/test_firmware_math.py builds and runs it.)
#include <cstdio>
#include <cstdlib>
#include "../motor_math.h"

using namespace motor;
static int fails = 0;
#define EQ(a, b) do { int _a = (a), _b = (b); if (_a != _b) { \
  std::printf("FAIL %s:%d  %s = %d, want %d\n", __FILE__, __LINE__, #a, _a, _b); \
  fails++; } } while (0)

int main() {
  // speedToDuty: 0 off, 1 -> minDuty, 255 -> 255, monotonic, clamped
  EQ(speedToDuty(0, 90), 0);
  EQ(speedToDuty(-5, 90), 0);
  EQ(speedToDuty(1, 90), 90);
  EQ(speedToDuty(255, 90), 255);
  EQ(speedToDuty(999, 90), 255);
  EQ(speedToDuty(128, 0), 127);
  EQ(speedToDuty(200, 90), 90 + 199 * 165 / 254);
  for (int s = 1, prev = 0; s <= 255; s++) {
    int d = speedToDuty(s, 90);
    if (d < prev) { std::printf("FAIL not monotonic at %d\n", s); fails++; }
    prev = d;
  }

  // applyTrim
  EQ(applyTrim(200, 100), 200);
  EQ(applyTrim(200, 90), 180);
  EQ(applyTrim(200, 10), 100);        // clamped to 50%
  EQ(applyTrim(0, 90), 0);
  EQ(applyTrim(1, 50), 1);            // a running wheel never trims to 0

  // rampStep: accelerate, cap at target
  EQ(rampStep(0, 200, 10), 10);
  EQ(rampStep(195, 200, 10), 200);
  EQ(rampStep(0, -200, 10), -10);
  EQ(rampStep(-195, -200, 10), -200);
  // decelerate
  EQ(rampStep(200, 100, 10), 190);
  EQ(rampStep(105, 100, 10), 100);
  EQ(rampStep(5, 0, 10), 0);
  EQ(rampStep(-5, 0, 10), 0);
  // reversal passes through exactly 0, never jumps sign
  EQ(rampStep(5, -200, 10), 0);
  EQ(rampStep(-5, 200, 10), 0);
  int cur = 200, steps = 0, sawZero = 0;
  while (cur != -200 && steps < 1000) {
    int next = rampStep(cur, -200, 7);
    if ((cur > 0 && next < 0) || (cur < 0 && next > 0)) {
      std::printf("FAIL sign jump %d -> %d\n", cur, next); fails++;
    }
    if (next == 0) sawZero = 1;
    cur = next; steps++;
  }
  EQ(cur, -200);
  EQ(sawZero, 1);
  // step 0 = instant
  EQ(rampStep(200, -200, 0), -200);

  // dead zone: start jumps to it, slow-down cuts out below it
  EQ(rampStep(0, 200, 3, 90), 90);
  EQ(rampStep(0, -200, 3, 90), -90);
  EQ(rampStep(0, 50, 3, 90), 50);       // target inside zone: go to target
  EQ(rampStep(92, 0, 3, 90), 0);        // 89 would hum: cut to 0
  EQ(rampStep(-92, 0, 3, 90), 0);
  EQ(rampStep(92, -200, 3, 90), 0);     // reversing: through 0, skipping zone
  EQ(rampStep(0, -200, 3, 90), -90);
  EQ(rampStep(150, 100, 3, 90), 147);   // outside zone: normal step
  EQ(rampStep(92, 50, 3, 90), 89);      // slowing to an in-zone target: step
  EQ(rampStep(100, 200, 3, 90), 103);
  cur = 255; steps = 0;                 // full reversal never lingers in zone
  while (cur != -255 && steps < 1000) {
    int next = rampStep(cur, -255, 3, 90);
    int m = next < 0 ? -next : next;
    if (m > 0 && m < 90) { std::printf("FAIL in dead zone: %d\n", next); fails++; }
    cur = next; steps++;
  }
  EQ(cur, -255);

  // stepFor
  EQ(stepFor(0, 2), 0);
  EQ(stepFor(150, 2), 3);
  EQ(stepFor(10000, 2), 1);

  if (fails) { std::printf("%d failure(s)\n", fails); return 1; }
  std::printf("motor_math: all tests passed\n");
  return 0;
}
