/*
  Two HC-SR04 sonars, sequential ping, one CSV-style serial frame:

      <pitch_mm>,<volume_mm>

  Invalid / timeout = -1

  Pitch sensor:  TRIG 8, ECHO 9
  Volume sensor: TRIG 10, ECHO 11

  Sequential trigger avoids 40 kHz crosstalk. Timeout is short so the
  pair can still run tens of Hz inside a ~0.4 m working range.
*/

const int PITCH_TRIG = 8;
const int PITCH_ECHO = 9;
const int VOL_TRIG = 10;
const int VOL_ECHO = 11;

const unsigned long ECHO_TIMEOUT_US = 4000;  // ~68 cm round-trip
const int GAP_MS = 8;

void setup() {
  Serial.begin(115200);
  pinMode(PITCH_TRIG, OUTPUT);
  pinMode(PITCH_ECHO, INPUT);
  pinMode(VOL_TRIG, OUTPUT);
  pinMode(VOL_ECHO, INPUT);
  digitalWrite(PITCH_TRIG, LOW);
  digitalWrite(VOL_TRIG, LOW);
}

long pingMm(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(3);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long us = pulseIn(echo, HIGH, ECHO_TIMEOUT_US);
  if (us == 0) {
    return -1;
  }
  return (long)(us * 0.1715f);
}

void loop() {
  long pitch = pingMm(PITCH_TRIG, PITCH_ECHO);
  delay(GAP_MS);
  long volume = pingMm(VOL_TRIG, VOL_ECHO);
  Serial.print(pitch);
  Serial.print(',');
  Serial.println(volume);
  delay(12);
}
