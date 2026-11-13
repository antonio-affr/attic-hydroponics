"""Sensor implementations and the factory that builds them from the config."""

from __future__ import annotations

import random
from zoneinfo import ZoneInfo

from collector.config import SensorSpec
from collector.sensors.base import Sensor, SensorReadError
from collector.sensors.grove import GroveSensor
from collector.sensors.simulated import SimulatedSensor

__all__ = ["Sensor", "SensorReadError", "build_sensor"]


def build_sensor(
    spec: SensorSpec,
    interval_seconds: float,
    tz: ZoneInfo,
    rng: random.Random | None = None,
) -> Sensor:
    if spec.type == "simulated":
        return SimulatedSensor(spec, interval_seconds, tz, rng=rng)
    if spec.type == "grove":
        return GroveSensor(spec)
    raise ValueError(f"Unknown sensor type: {spec.type}")
