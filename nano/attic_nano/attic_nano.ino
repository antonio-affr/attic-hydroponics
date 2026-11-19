// Attic Nano: the wired sensor hub (see nano/README.md).
//
// Step 1, heartbeat only: the built-in LED blinks and, every 2 seconds, one
// JSON line goes over USB serial (115200 baud), e.g.
//   {"device":"attic_nano_01","uptime_s":12}
// Sensors are added to this line one at a time.

const char DEVICE[] = "attic_nano_01";
const unsigned long REPORT_EVERY_MS = 2000;

unsigned long lastReport = 0;

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  unsigned long now = millis();
  if (now - lastReport >= REPORT_EVERY_MS) {
    lastReport = now;
    digitalWrite(LED_BUILTIN, HIGH);
    Serial.print(F("{\"device\":\""));
    Serial.print(DEVICE);
    Serial.print(F("\",\"uptime_s\":"));
    Serial.print(now / 1000);
    Serial.println(F("}"));
    delay(100);
    digitalWrite(LED_BUILTIN, LOW);
  }
}
