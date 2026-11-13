"""Grove temperature and humidity sensor: Sensirion SHT3x (SHT30 / SHT31 / SHT35) over I2C.

No extra library: the driver talks to the Linux I2C device file directly, exactly
as `i2ctransfer` does by hand:

    send "measure" (0x24 0x00: high repeatability, no clock stretching)
    wait about 20 ms
    read 6 bytes: temperature (2) + checksum (1), humidity (2) + checksum (1)

Temperature and humidity come from ONE chip, so both sensors in the config share
one driver per (bus, address). The first of the two to be read in a cycle
measures; the other reuses that measurement if it is younger than a few seconds.
The chip is therefore read once per cycle, and both values come from the same
instant.

Any problem (no answer, wrong checksum) raises SensorReadError: the collector logs
it, publishes nothing, and Home Assistant marks the sensor stale. A made-up value
is never returned.

Config (`options:` of a `type: grove` sensor in config/collector.pi.yaml):

    model: sht3x            # the only model supported so far
    quantity: temperature   # or: humidity
    bus: 1                  # /dev/i2c-1, the Grove / GPIO header bus
    address: 0x44           # 0x45 if the board's address pad is changed
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from collector.config import ConfigError, SensorSpec
from collector.sensors.base import Sensor, SensorReadError

MEASURE_HIGH_REPEATABILITY = bytes([0x24, 0x00])
MEASUREMENT_WAIT_SECONDS = 0.02  # datasheet maximum is 15.5 ms for high repeatability
SHARE_WINDOW_SECONDS = 5.0  # temperature and humidity of one cycle reuse one measurement
ATTEMPTS = 2  # one quiet retry: a single glitch on a long cable should not cost a reading
I2C_SLAVE = 0x0703  # Linux ioctl: "the next reads/writes on this file go to this address"

QUANTITIES = ("temperature", "humidity")
SUPPORTED_MODELS = ("sht3x",)


@dataclass(frozen=True)
class Sht3xReading:
    temperature: float  # °C
    humidity: float  # % relative humidity


def crc8(data: bytes) -> int:
    """Sensirion checksum: CRC-8, polynomial 0x31, initial value 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode(raw: bytes) -> Sht3xReading:
    """Turn the chip's 6 bytes into °C and %RH, checking both checksums."""
    if len(raw) != 6:
        raise SensorReadError(f"SHT3x: expected 6 bytes, got {len(raw)}")
    for name, word, checksum in (("temperature", raw[0:2], raw[2]), ("humidity", raw[3:5], raw[5])):
        if crc8(word) != checksum:
            raise SensorReadError(f"SHT3x: {name} checksum mismatch (bytes {raw.hex(' ')}): noise on the cable?")
    t_raw = int.from_bytes(raw[0:2], "big")
    h_raw = int.from_bytes(raw[3:5], "big")
    # Datasheet formulas. Humidity is 0-100 % by construction (raw 0 to 65535).
    return Sht3xReading(temperature=-45 + 175 * t_raw / 65535, humidity=100 * h_raw / 65535)


class I2CTransport(Protocol):
    def exchange(self, command: bytes, wait_seconds: float, read_length: int) -> bytes: ...


class LinuxI2C:
    """Raw I2C through /dev/i2c-N. Opened per measurement, so unplugging and replugging just works."""

    def __init__(self, bus: int, address: int) -> None:
        self.bus = bus
        self.address = address
        self.path = f"/dev/i2c-{bus}"

    def exchange(self, command: bytes, wait_seconds: float, read_length: int) -> bytes:
        import fcntl  # Linux only; imported here so the rest of the module loads on Windows for tests

        fd = os.open(self.path, os.O_RDWR)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, self.address)
            os.write(fd, command)
            time.sleep(wait_seconds)
            return os.read(fd, read_length)
        finally:
            os.close(fd)


class Sht3x:
    """One physical chip. Thread-safe; caches a measurement for SHARE_WINDOW_SECONDS."""

    def __init__(
        self,
        transport: I2CTransport,
        description: str,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._description = description
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: Sht3xReading | None = None
        self._last_at = float("-inf")

    def measure(self) -> Sht3xReading:
        with self._lock:
            if self._last is not None and self._clock() - self._last_at < SHARE_WINDOW_SECONDS:
                return self._last
            self._last = None
            error: SensorReadError | None = None
            for attempt in range(ATTEMPTS):
                if attempt:
                    self._sleep(0.1)
                try:
                    raw = self._transport.exchange(MEASURE_HIGH_REPEATABILITY, MEASUREMENT_WAIT_SECONDS, 6)
                    reading = decode(raw)
                except OSError as exc:
                    error = SensorReadError(f"SHT3x at {self._description} did not answer ({exc}): is it plugged in?")
                    continue
                except SensorReadError as exc:
                    error = exc
                    continue
                self._last, self._last_at = reading, self._clock()
                return reading
            assert error is not None
            raise error


_chips: dict[tuple[int, int], Sht3x] = {}
_chips_lock = threading.Lock()


def shared_chip(bus: int, address: int) -> Sht3x:
    """The single Sht3x object for this bus and address, created on first use."""
    with _chips_lock:
        chip = _chips.get((bus, address))
        if chip is None:
            chip = Sht3x(LinuxI2C(bus, address), f"0x{address:02x} on /dev/i2c-{bus}")
            _chips[(bus, address)] = chip
        return chip


def _int_option(spec: SensorSpec, name: str, default: int) -> int:
    value = spec.options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"Sensor '{spec.key}': options.{name} must be a whole number, got {value!r}")
    return value


class GroveSensor(Sensor):
    def __init__(self, spec: SensorSpec, chip: Sht3x | None = None) -> None:
        super().__init__(spec)
        options = spec.options
        model = str(options.get("model", "sht3x")).lower()
        if model not in SUPPORTED_MODELS:
            raise ConfigError(f"Sensor '{spec.key}': options.model {model!r} not supported (known: {', '.join(SUPPORTED_MODELS)})")
        quantity = options.get("quantity")
        if quantity not in QUANTITIES:
            raise ConfigError(f"Sensor '{spec.key}': options.quantity must be one of {', '.join(QUANTITIES)}, got {quantity!r}")
        self.quantity: str = quantity
        bus = _int_option(spec, "bus", 1)
        address = _int_option(spec, "address", 0x44)
        self.chip = chip if chip is not None else shared_chip(bus, address)

    def read(self) -> float:
        reading = self.chip.measure()
        return reading.temperature if self.quantity == "temperature" else reading.humidity
