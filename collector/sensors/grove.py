"""Real Grove sensor on the Raspberry Pi. STUB: to be filled in on the Pi day.

TODO (Pi day):
1. Paste the reading logic from the existing Python script into read() below.
2. Return the value for self.key ("air_temperature" in °C, "humidity" in %).
   Temperature and humidity usually come from ONE physical sensor: open the
   driver once (e.g. a module-level helper keyed by I2C address) and let both
   GroveSensor instances share it, so the chip is not read twice per cycle.
3. On any failure (no device, timeout, bad checksum), raise SensorReadError
   with a clear message. Never return a made-up value: the collector logs the
   error, skips publishing, and Home Assistant shows the sensor as stale.
4. Put sensor-specific settings (model, I2C address, port) in the sensor's
   `options:` block in config/collector.pi.yaml and read them from
   self.spec.options.
5. Add the hardware library to requirements.txt (Pi only if needed).
See docs/REAL_SENSORS.md.
"""

from __future__ import annotations

from collector.sensors.base import Sensor, SensorReadError


class GroveSensor(Sensor):
    def read(self) -> float:
        raise SensorReadError(f"{self.key}: Grove driver not implemented yet (see collector/sensors/grove.py)")
