"""The Nano reader: lines in, one field per sensor out, and every failure is an error, never a made-up value."""

from __future__ import annotations

import threading

import pytest

from collector.config import ConfigError, SensorSpec
from collector.sensors.base import SensorReadError
from collector.sensors.nano import NanoLink, NanoSensor, resolve_port

# A real line from the Nano on the desk, 2026-11-19.
REAL_LINE = (
    b'{"device":"attic_nano_01","uptime_s":8,"light_1_lux":46.7,"light_2_lux":41.7,'
    b'"light_3_lux":null,"water_temperature_c":21.69}\r\n'
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def spec(field: str, **options: object) -> SensorSpec:
    return SensorSpec(
        key=field, type="nano", name=field, unit="", valid_range=(0, 100000),
        options={"port": "COM_TEST", "field": field, **options},
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def link(clock: FakeClock) -> NanoLink:
    return NanoLink("COM_TEST", opener=lambda port: pytest.fail("must not open a port"), clock=clock)


def test_reads_each_field_from_the_latest_line(link: NanoLink) -> None:
    link.handle_line(REAL_LINE)
    assert NanoSensor(spec("light_1_lux"), link).read() == 46.7
    assert NanoSensor(spec("water_temperature_c"), link).read() == 21.69


def test_null_field_is_an_error_not_zero(link: NanoLink) -> None:
    link.handle_line(REAL_LINE)
    with pytest.raises(SensorReadError, match="did not answer"):
        NanoSensor(spec("light_3_lux"), link).read()


def test_missing_field_is_an_error(link: NanoLink) -> None:
    link.handle_line(REAL_LINE)
    with pytest.raises(SensorReadError, match="does not send"):
        NanoSensor(spec("tank_distance_mm"), link).read()


def test_no_line_yet_is_an_error(link: NanoLink) -> None:
    with pytest.raises(SensorReadError, match="no data"):
        NanoSensor(spec("light_1_lux"), link).read()


def test_silent_nano_goes_stale(link: NanoLink, clock: FakeClock) -> None:
    link.handle_line(REAL_LINE)
    sensor = NanoSensor(spec("light_1_lux", max_age_seconds=30), link)
    clock.now += 29
    assert sensor.read() == 46.7
    clock.now += 2
    with pytest.raises(SensorReadError, match="sent nothing for 31 s"):
        sensor.read()


def test_garbage_lines_are_ignored(link: NanoLink) -> None:
    link.handle_line(REAL_LINE)
    for junk in (b"\xff\xfe\x00", b"", b'{"device":"attic_nano_01","uptime', b"[1, 2]", b"hello"):
        link.handle_line(junk)
    assert NanoSensor(spec("light_1_lux"), link).read() == 46.7


def test_non_numeric_value_is_an_error(link: NanoLink) -> None:
    link.handle_line(b'{"light_1_lux": "bright", "flag": true}')
    with pytest.raises(SensorReadError, match="not a number"):
        NanoSensor(spec("light_1_lux"), link).read()
    with pytest.raises(SensorReadError, match="not a number"):
        NanoSensor(spec("flag"), link).read()


def test_port_and_field_are_required(link: NanoLink) -> None:
    bad = SensorSpec(key="x", type="nano", name="x", unit="", valid_range=(0, 1), options={"port": "COM6"})
    with pytest.raises(ConfigError, match="'port' and 'field'"):
        NanoSensor(bad, link)


def test_plain_port_is_kept_and_unmatched_pattern_fails() -> None:
    assert resolve_port("COM6") == "COM6"
    with pytest.raises(OSError, match="no serial port matches"):
        resolve_port("/no/such/dir/usb-FTDI_*")


class ScriptedSource:
    """Plays lines, then fails like an unplugged cable."""

    def __init__(self, lines: list[bytes], done: threading.Event) -> None:
        self.lines = list(lines)
        self.done = done
        self.closed = False

    def readline(self) -> bytes:
        if self.lines:
            return self.lines.pop(0)
        self.done.set()
        raise OSError("device disconnected")

    def close(self) -> None:
        self.closed = True


def test_background_thread_reads_and_survives_a_disconnect(clock: FakeClock) -> None:
    done = threading.Event()
    sources: list[ScriptedSource] = []

    def opener(port: str) -> ScriptedSource:
        source = ScriptedSource([REAL_LINE], done)
        sources.append(source)
        return source

    link = NanoLink("COM_TEST", opener=opener, clock=clock)
    link.start()
    try:
        assert done.wait(5), "the reader thread never reached the end of its lines"
        assert NanoSensor(spec("light_2_lux"), link).read() == 41.7
    finally:
        link.stop()
    assert sources and sources[0].closed
