/*
  Serva z PC: ESP32 + PCA9685, libovolny kanal 0-15.

  I2C: SDA=21, SCL=25
  Serial 115200:
    ping           I2C scan + stav
    12 90
    map 1 4
    lim 60 120
    nudge 4
    mid

  Mechanika:
    kanal 12 = pan vlevo/vpravo
    kanal 15 = tilt nahoru/dolu: 0 = strop, 110 = primo dopredu, 159 = dolu

*/

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

const int PIN_SDA = 21;
const int PIN_SCL = 25;

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

const int SERVO_MIN = 110;  // ~0.54 ms — nenechat moc siroke, 180 by skocilo na stred
const int SERVO_MAX = 460;  // ~2.25 ms
const int FULL_MIN = 0;
const int FULL_MAX = 180;
const int MID_A = 90;    // pan stred
const int MID_B = 110;   // tilt primo dopredu (0 = strop)

int chA = 12;
int chB = 15;
int limMin = 0;
int limMax = 159;
bool pcaOk = false;

void setDeg(int ch, int deg, int lo, int hi);

void i2cScan() {
  Serial.println("--- I2C scan SDA=21 SCL=25 ---");
  int found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission();
    if (err == 0) {
      Serial.printf("  found 0x%02X\n", addr);
      found++;
      if (addr == 0x40) {
        pcaOk = true;
      }
    }
  }
  if (found == 0) {
    pcaOk = false;
    Serial.println("  NIC. Zkontroluj VCC=3V3, GND, SDA=21, SCL=25.");
  } else if (!pcaOk) {
    Serial.println("  Cip je, ale ne 0x40 (jumper A0-A5?).");
  } else {
    Serial.println("  PCA9685 0x40 OK");
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);
  delay(20);
  i2cScan();
  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(50);
  delay(10);
  if (pcaOk) {
    setDeg(chA, MID_A, FULL_MIN, FULL_MAX);
    setDeg(chB, MID_B, limMin, limMax);
  }
  Serial.println("SERVO FW ready. OE musis dat na GND.");
}

void setDeg(int ch, int deg, int lo, int hi) {
  if (ch < 0 || ch > 15) {
    return;
  }
  deg = constrain(deg, lo, hi);
  int pulse = map(deg, 0, 180, SERVO_MIN, SERVO_MAX);
  pwm.setPWM((uint8_t)ch, 0, pulse);
}

void loop() {
  if (!Serial.available()) {
    delay(5);
    return;
  }
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) {
    return;
  }
  if (line == "ping" || line == "?") {
    i2cScan();
    return;
  }
  if (line == "s" || line == "mid") {
    setDeg(chA, MID_A, FULL_MIN, FULL_MAX);
    setDeg(chB, MID_B, limMin, limMax);
    Serial.printf("mid A=%d B=%d pca=%d\n", chA, chB, pcaOk ? 1 : 0);
    return;
  }
  if (line.startsWith("map ")) {
    int a = 0;
    int b = 0;
    if (sscanf(line.c_str(), "map %d %d", &a, &b) == 2 && a >= 0 && a <= 15 && b >= 0 && b <= 15) {
      chA = a;
      chB = b;
      setDeg(chA, MID_A, FULL_MIN, FULL_MAX);
      setDeg(chB, MID_B, limMin, limMax);
      Serial.printf("map A=%d B=%d\n", chA, chB);
    }
    return;
  }
  if (line.startsWith("lim ")) {
    int a = 0;
    int b = 0;
    if (sscanf(line.c_str(), "lim %d %d", &a, &b) == 2) {
      limMin = constrain(a, 0, 170);
      limMax = constrain(b, limMin + 5, 180);
      setDeg(chB, constrain(MID_B, limMin, limMax), limMin, limMax);
      Serial.printf("lim %d %d\n", limMin, limMax);
    }
    return;
  }
  if (line.startsWith("nudge ")) {
    int ch = 0;
    if (sscanf(line.c_str(), "nudge %d", &ch) == 1 && ch >= 0 && ch <= 15) {
      int lo = (ch == chB) ? limMin : FULL_MIN;
      int hi = (ch == chB) ? limMax : FULL_MAX;
      int home = (ch == chB) ? MID_B : MID_A;
      int a1 = constrain(home - 20, lo, hi);
      int a2 = constrain(home + 20, lo, hi);
      setDeg(ch, a1, lo, hi);
      delay(280);
      setDeg(ch, a2, lo, hi);
      delay(280);
      setDeg(ch, home, lo, hi);
      Serial.printf("nudge %d pca=%d\n", ch, pcaOk ? 1 : 0);
    }
    return;
  }
  int ch = 0;
  int deg = 0;
  if (sscanf(line.c_str(), "%d %d", &ch, &deg) == 2 && ch >= 0 && ch <= 15) {
    int lo = FULL_MIN;
    int hi = FULL_MAX;
    if (ch == chB) {
      lo = limMin;
      hi = limMax;
    }
    int v = constrain(deg, lo, hi);
    setDeg(ch, v, lo, hi);
    Serial.printf("%d %d pca=%d\n", ch, v, pcaOk ? 1 : 0);
  }
}
