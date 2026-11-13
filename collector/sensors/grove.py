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
    mux_channel: 6          # only behind a TCA9548A hub: the channel (0-7) it is plugged into
    mux_address: 0x70       # the hub's own address (default 0x70)

Behind a TCA9548A 8-channel I2C hub, several sensors can share the address 0x44,
one per channel. The hub keeps every channel closed at power-up; before each
measurement the driver opens the sensor's channel (one byte, 1 << channel, sent
to the hub) and keeps the hub locked until the measurement is read, so two
sensors can never answer at once.
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

MUX_CHANNELS = range(8)
MUX_ADDRESSES = range(0x70, 0x78)  # set by the hub's A0-A2 pads

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


class I2CWriter(Protocol):
    def write(self, data: bytes) -> None: ...


class LinuxI2C:
    """Raw I2C through /dev/i2c-N. Opened per measurement, so unplugging and replugging just works."""

    def __init__(self, bus: int, address: int) -> None:
        self.bus = bus
        self.address = address
        self.path = f"/dev/i2c-{bus}"

    def _open(self) -> int:
        import fcntl  # Linux only; imported here so the rest of the module loads on Windows for tests

        fd = os.open(self.path, os.O_RDWR)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, self.address)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def exchange(self, command: bytes, wait_seconds: float, read_length: int) -> bytes:
        fd = self._open()
        try:
            os.write(fd, command)
            time.sleep(wait_seconds)
            return os.read(fd, read_length)
        finally:
            os.close(fd)

    def write(self, data: bytes) -> None:
        fd = self._open()
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


class Tca9548a:
    """The 8-channel I2C hub. One object per hub; its lock spans "open channel + measure"."""

    def __init__(self, transport: I2CWriter) -> None:
        self._transport = transport
        self.lock = threading.Lock()

    def select(self, channel: int) -> None:
        """Open exactly one channel (the others close). Call with self.lock held."""
        self._transport.write(bytes([1 << channel]))


class MuxChannelTransport:
    """A sensor's transport when it sits behind the hub: open its channel, then talk to it."""

    def __init__(self, mux: Tca9548a, channel: int, inner: I2CTransport) -> None:
        self._mux = mux
        self._channel = channel
        self._inner = inner

    def exchange(self, command: bytes, wait_seconds: float, read_length: int) -> bytes:
        with self._mux.lock:
            self._mux.select(self._channel)
            return self._inner.exchange(command, wait_seconds, read_length)


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


_chips: dict[tuple[int, int, int | None, int | None], Sht3x] = {}
_muxes: dict[tuple[int, int], Tca9548a] = {}
_registry_lock = threading.Lock()


def shared_chip(bus: int, address: int, mux_address: int | None = None, mux_channel: int | None = None) -> Sht3x:
    """The single Sht3x object for this place on the bus (hub channel included), created on first use."""
    key = (bus, address, mux_address, mux_channel)
    with _registry_lock:
        chip = _chips.get(key)
        if chip is None:
            transport: I2CTransport = LinuxI2C(bus, address)
            description = f"0x{address:02x} on /dev/i2c-{bus}"
            if mux_address is not None and mux_channel is not None:
                mux = _muxes.get((bus, mux_address))
                if mux is None:
                    mux = _muxes[(bus, mux_address)] = Tca9548a(LinuxI2C(bus, mux_address))
                transport = MuxChannelTransport(mux, mux_channel, transport)
                description += f", channel {mux_channel} of the hub at 0x{mux_address:02x}"
            chip = _chips[key] = Sht3x(transport, description)
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
        mux_channel: int | None = None
        mux_address: int | None = None
        if "mux_channel" in options:
            mux_channel = _int_option(spec, "mux_channel", 0)
            if mux_channel not in MUX_CHANNELS:
                raise ConfigError(f"Sensor '{spec.key}': options.mux_channel must be 0 to 7, got {mux_channel}")
            mux_address = _int_option(spec, "mux_address", 0x70)
            if mux_address not in MUX_ADDRESSES:
                raise ConfigError(f"Sensor '{spec.key}': options.mux_address must be 0x70 to 0x77, got 0x{mux_address:02x}")
        elif "mux_address" in options:
            raise ConfigError(f"Sensor '{spec.key}': options.mux_address needs options.mux_channel (which hub channel?)")
        self.chip = chip if chip is not None else shared_chip(bus, address, mux_address, mux_channel)

    def read(self) -> float:
        reading = self.chip.measure()
        return reading.temperature if self.quantity == "temperature" else reading.humidity
