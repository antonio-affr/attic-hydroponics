"""SHT3x driver tests. No hardware needed: a fake I2C transport replays bytes.

REAL_BYTES are the 6 bytes the attic sensor returned on 2026-11-13, read by hand with
`i2ctransfer -y 1 w2@0x44 0x24 0x00` then `i2ctransfer -y 1 r6@0x44`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from collector.config import ConfigError, SensorSpec, parse_config
from collector.sensors import SensorReadError, build_sensor
from collector.sensors.grove import GroveSensor, Sht3x, crc8, decode

REAL_BYTES = bytes([0x63, 0x39, 0xB4, 0xAF, 0xDB, 0x09])
PI_CONFIG = Path(__file__).resolve().parent.parent / "config" / "collector.pi.yaml"


class FakeTransport:
    """Returns the queued responses in order; an exception in the queue is raised."""

    def __init__(self, *responses: bytes | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    def exchange(self, command: bytes, wait_seconds: float, read_length: int) -> bytes:
        assert command == bytes([0x24, 0x00]) and read_length == 6
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_chip(*responses: bytes | Exception) -> tuple[Sht3x, FakeTransport, FakeClock]:
    transport, clock = FakeTransport(*responses), FakeClock()
    return Sht3x(transport, "test chip", clock=clock, sleep=lambda _: None), transport, clock


def spec(key: str, quantity: str) -> SensorSpec:
    return SensorSpec(key=key, type="grove", name=key, unit="", valid_range=(-100, 200), options={"model": "sht3x", "quantity": quantity})


def test_crc_matches_the_datasheet_example():
    assert crc8(bytes([0xBE, 0xEF])) == 0x92


def test_real_attic_bytes_decode_to_22_8_degrees_and_68_7_percent():
    reading = decode(REAL_BYTES)
    assert reading.temperature == pytest.approx(22.83, abs=0.01)
    assert reading.humidity == pytest.approx(68.69, abs=0.01)


def test_a_corrupted_byte_is_rejected_not_published():
    corrupted = bytes([0x63, 0x38]) + REAL_BYTES[2:]
    with pytest.raises(SensorReadError, match="temperature checksum"):
        decode(corrupted)


def test_temperature_and_humidity_share_one_measurement_per_cycle():
    chip, transport, _ = make_chip(REAL_BYTES)
    temperature = GroveSensor(spec("air_temperature", "temperature"), chip=chip)
    humidity = GroveSensor(spec("humidity", "humidity"), chip=chip)
    assert temperature.read() == pytest.approx(22.83, abs=0.01)
    assert humidity.read() == pytest.approx(68.69, abs=0.01)
    assert transport.calls == 1


def test_the_next_cycle_measures_again():
    chip, transport, clock = make_chip(REAL_BYTES, REAL_BYTES)
    chip.measure()
    clock.now += 300
    chip.measure()
    assert transport.calls == 2


def test_one_glitch_is_retried_quietly():
    chip, transport, _ = make_chip(OSError(121, "Remote I/O error"), REAL_BYTES)
    assert chip.measure().temperature == pytest.approx(22.83, abs=0.01)
    assert transport.calls == 2


def test_an_unplugged_sensor_gives_a_clear_error_and_is_not_cached():
    chip, transport, _ = make_chip(OSError(121, "Remote I/O error"), OSError(121, "Remote I/O error"), REAL_BYTES)
    with pytest.raises(SensorReadError, match="plugged in"):
        chip.measure()
    assert chip.measure().humidity == pytest.approx(68.69, abs=0.01)  # plugged back: reads again at once
    assert transport.calls == 3


@pytest.mark.parametrize(
    "options, message",
    [
        ({"model": "sht3x", "quantity": "pressure"}, "quantity"),
        ({"model": "dht22", "quantity": "temperature"}, "not supported"),
        ({"model": "sht3x", "quantity": "humidity", "address": "0x44"}, "whole number"),
    ],
)
def test_config_mistakes_fail_at_startup(options, message):
    bad = SensorSpec(key="humidity", type="grove", name="Humidity", unit="%", valid_range=(0, 100), options=options)
    with pytest.raises(ConfigError, match=message):
        GroveSensor(bad)


def test_the_pi_config_builds_two_sensors_on_one_chip():
    raw = yaml.safe_load(PI_CONFIG.read_text(encoding="utf-8"))
    config = parse_config(raw, env={}, require_credentials=False)
    sensors = {s.key: build_sensor(s, config.interval_seconds, config.tz) for s in config.enabled_sensors}
    temperature, humidity = sensors["air_temperature"], sensors["humidity"]
    assert isinstance(temperature, GroveSensor) and isinstance(humidity, GroveSensor)
    assert (temperature.quantity, humidity.quantity) == ("temperature", "humidity")
    assert temperature.chip is humidity.chip
