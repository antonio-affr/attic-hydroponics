"""After Home Assistant (re)starts, the collector resends the latest readings instead of
leaving the sensors "Unavailable" until the next cycle (up to 5 minutes on the Pi)."""

from __future__ import annotations

from pathlib import Path

import yaml

import collector.main as main
from collector.config import parse_config
from collector.main import Collector
from collector.sensors.base import Sensor

SIMULATOR_CONFIG = Path(__file__).resolve().parent.parent / "config" / "collector.simulator.yaml"


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, retain: bool = False, qos: int = 0) -> bool:
        self.messages.append((topic, payload))
        return True


class FixedSensor(Sensor):
    def read(self) -> float:
        return 22.8


def make_collector(tmp_path) -> tuple[Collector, FakePublisher]:
    raw = yaml.safe_load(SIMULATOR_CONFIG.read_text(encoding="utf-8"))
    raw["overrides_file"] = str(tmp_path / "overrides.json")
    config = parse_config(raw, env={}, require_credentials=False)
    publisher = FakePublisher()
    return Collector(config, [FixedSensor(config.sensor("air_temperature"))], publisher), publisher


def test_the_latest_reading_is_resent_unchanged(tmp_path):
    collector, publisher = make_collector(tmp_path)
    collector.collect_once()
    original = publisher.messages[-1]
    assert collector.resend_latest() == 1
    assert publisher.messages[-1] == original  # same value and same timestamp of the real reading


def test_nothing_to_resend_before_the_first_reading(tmp_path):
    collector, publisher = make_collector(tmp_path)
    assert collector.resend_latest() == 0
    assert publisher.messages == []


def test_a_stale_reading_is_not_resent(tmp_path, monkeypatch):
    collector, _ = make_collector(tmp_path)
    collector.collect_once()
    later = main.time.monotonic() + collector.config.stale_after_seconds + 1
    monkeypatch.setattr(main.time, "monotonic", lambda: later)
    assert collector.resend_latest() == 0
