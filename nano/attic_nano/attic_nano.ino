// Attic Nano: the wired sensor hub (see nano/README.md).
//
// Every 2 seconds the built-in LED blinks and one JSON line goes over USB
// serial (115200 baud), e.g.
//   {"device":"attic_nano_01","uptime_s":12,"light_lux":523.3,"water_temperature_c":21.4}
// A sensor that does not answer sends null, so the Pi can tell "dark" (0)
// from "sensor missing" (null).
//
// Sensors so far:
//   BH1750 light sensor, I2C (SDA = A4, SCL = A5), ADDR unconnected = 0x23.
//   Wires on the ARCELI dome module: red VCC (5V), brown SCL, white SDA,
//   black GND, yellow ADDR.
//   DS18B20 waterproof probe, 1-Wire on D4: red VCC (5V), black GND, yellow
//   DATA, with a 4.7 kOhm pull-up resistor between DATA and 5V.
//
// Libraries: OneWire, DallasTemperature (see nano/README.md).

#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char DEVICE[] = "attic_nano_01";
const unsigned long REPORT_EVERY_MS = 2000;

const uint8_t BH1750_ADDR = 0x23;
const uint8_t BH1750_POWER_ON = 0x01;
const uint8_t BH1750_CONTINUOUS_HIGH_RES = 0x10;  // 1 lx resolution, 120 ms

const uint8_t WATER_TEMP_PIN = 4;

OneWire oneWire(WATER_TEMP_PIN);
DallasTemperature waterProbe(&oneWire);

unsigned long lastReport = 0;
bool lightStarted = false;

bool bh1750Command(uint8_t cmd) {
  Wire.beginTransmission(BH1750_ADDR);
  Wire.write(cmd);
  return Wire.endTransmission() == 0;
}

bool startLight() {
  return bh1750Command(BH1750_POWER_ON) && bh1750Command(BH1750_CONTINUOUS_HIGH_RES);
}

// Returns lux, or a negative number when the sensor does not answer.
float readLux() {
  if (!lightStarted) {
    lightStarted = startLight();  // also recovers a sensor plugged in late
    if (!lightStarted) return -1;
    delay(180);
  }
  if (Wire.requestFrom(BH1750_ADDR, (uint8_t)2) != 2) {
    lightStarted = false;
    return -1;
  }
  uint16_t raw = (uint16_t)Wire.read() << 8;
  raw |= Wire.read();
  return raw / 1.2;
}

// The conversion is started one cycle ahead and read the next, so the Nano
// never waits the 750 ms it takes. Returns -1000 when there is no valid
// reading: no probe, a broken wire, or 85.0 (the value before a first
// conversion).
float readWaterTemp() {
  float c = waterProbe.getTempCByIndex(0);
  waterProbe.requestTemperatures();
  if (c == DEVICE_DISCONNECTED_C || c == 85.0) return -1000;
  return c;
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  Wire.begin();
  Wire.setWireTimeout(25000, true);  // a stuck bus must not freeze the Nano
  lightStarted = startLight();
  waterProbe.begin();
  waterProbe.setResolution(12);
  waterProbe.setWaitForConversion(false);
  waterProbe.requestTemperatures();
}

void loop() {
  unsigned long now = millis();
  if (now - lastReport >= REPORT_EVERY_MS) {
    lastReport = now;
    digitalWrite(LED_BUILTIN, HIGH);
    float lux = readLux();
    Serial.print(F("{\"device\":\""));
    Serial.print(DEVICE);
    Serial.print(F("\",\"uptime_s\":"));
    Serial.print(now / 1000);
    Serial.print(F(",\"light_lux\":"));
    if (lux < 0) Serial.print(F("null"));
    else Serial.print(lux, 1);
    float water = readWaterTemp();
    Serial.print(F(",\"water_temperature_c\":"));
    if (water < -999) Serial.print(F("null"));
    else Serial.print(water, 2);
    Serial.println(F("}"));
    delay(100);
    digitalWrite(LED_BUILTIN, LOW);
  }
}
