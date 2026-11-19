// Attic Nano: the wired sensor hub (see nano/README.md and docs/WIRING.md).
//
// Every 2 seconds the built-in LED blinks and one JSON line goes over USB
// serial (115200 baud), e.g.
//   {"device":"attic_nano_01","uptime_s":12,"light_1_lux":523.3,
//    "light_2_lux":498.3,"light_3_lux":40.8,"water_temperature_c":21.4}
// (one line in practice). A sensor that does not answer sends null, so the
// Pi can tell "dark" (0) from "sensor missing" (null).
//
// Sensors:
//   3 x BH1750 light sensor, ADDR unconnected, so all at 0x23. Each has its
//   own pair of pins, so they never share a bus and need no hub:
//     light 1: SDA A4, SCL A5 (the Nano's hardware I2C)
//     light 2: SDA D2, SCL D3 (software I2C)
//     light 3: SDA D5, SCL D6 (software I2C)
//   Wires on the ARCELI dome module: red VCC (5V), brown SCL, white SDA,
//   black GND, yellow ADDR (unconnected).
//   DS18B20 waterproof probe, 1-Wire on D4: red VCC (5V), black GND, yellow
//   DATA, with a 4.7 kOhm pull-up resistor between DATA and 5V.
//
// Libraries: OneWire, DallasTemperature, SoftwareWire (see nano/README.md).

#include <Wire.h>
#include <SoftwareWire.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char DEVICE[] = "attic_nano_01";
const unsigned long REPORT_EVERY_MS = 2000;

const uint8_t BH1750_ADDR = 0x23;
const uint8_t BH1750_POWER_ON = 0x01;
const uint8_t BH1750_CONTINUOUS_HIGH_RES = 0x10;  // 1 lx resolution, 120 ms

const uint8_t WATER_TEMP_PIN = 4;

SoftwareWire light2Bus(2, 3);  // SDA, SCL
SoftwareWire light3Bus(5, 6);

OneWire oneWire(WATER_TEMP_PIN);
DallasTemperature waterProbe(&oneWire);

unsigned long lastReport = 0;
bool lightStarted[3] = {false, false, false};

// Works for both the hardware bus (Wire) and the software ones.
template <typename Bus>
bool bh1750Command(Bus &bus, uint8_t cmd) {
  bus.beginTransmission(BH1750_ADDR);
  bus.write(cmd);
  return bus.endTransmission() == 0;
}

template <typename Bus>
bool startLight(Bus &bus) {
  return bh1750Command(bus, BH1750_POWER_ON) &&
         bh1750Command(bus, BH1750_CONTINUOUS_HIGH_RES);
}

// Returns lux, or a negative number when the sensor does not answer.
// A sensor plugged in late is started on the next cycle.
template <typename Bus>
float readLux(Bus &bus, bool &started) {
  if (!started) {
    started = startLight(bus);
    if (!started) return -1;
    delay(180);
  }
  if (bus.requestFrom(BH1750_ADDR, (uint8_t)2) != 2) {
    started = false;
    return -1;
  }
  uint16_t raw = (uint16_t)bus.read() << 8;
  raw |= bus.read();
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

void printLux(const __FlashStringHelper *key, float lux) {
  Serial.print(key);
  if (lux < 0) Serial.print(F("null"));
  else Serial.print(lux, 1);
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  Wire.begin();
  Wire.setWireTimeout(25000, true);  // a stuck bus must not freeze the Nano
  light2Bus.begin();
  light3Bus.begin();
  light2Bus.setTimeout(25);
  light3Bus.setTimeout(25);
  lightStarted[0] = startLight(Wire);
  lightStarted[1] = startLight(light2Bus);
  lightStarted[2] = startLight(light3Bus);
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
    float lux1 = readLux(Wire, lightStarted[0]);
    float lux2 = readLux(light2Bus, lightStarted[1]);
    float lux3 = readLux(light3Bus, lightStarted[2]);
    float water = readWaterTemp();

    Serial.print(F("{\"device\":\""));
    Serial.print(DEVICE);
    Serial.print(F("\",\"uptime_s\":"));
    Serial.print(now / 1000);
    printLux(F(",\"light_1_lux\":"), lux1);
    printLux(F(",\"light_2_lux\":"), lux2);
    printLux(F(",\"light_3_lux\":"), lux3);
    Serial.print(F(",\"water_temperature_c\":"));
    if (water < -999) Serial.print(F("null"));
    else Serial.print(water, 2);
    Serial.println(F("}"));
    delay(100);
    digitalWrite(LED_BUILTIN, LOW);
  }
}
