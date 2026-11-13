"""Realistic fake sensors: slow drift around a daily curve, plus optional spikes.

Two behaviours, chosen by the options in the config:

- Drifting (temperature, humidity): every reading moves a little towards a
  daily curve (mean +/- daily_amplitude, peaking at peak_hour) plus random noise.
- Draining (reservoir level): drops by drain_per_hour, and is topped up to
  refill_to when it falls below refill_below.

spike_probability adds a one-off jump of spike_size (not remembered), to test
alerts and validation. failure_probability makes read() fail now and then.
"""

from __future__ import annotations

import math
import random
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from collector.config import SensorSpec
from collector.sensors.base import Sensor, SensorReadError


class SimulatedSensor(Sensor):
    def __init__(
        self,
        spec: SensorSpec,
        interval_seconds: float,
        tz: ZoneInfo,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(spec)
        low, high = spec.valid_range
        options = spec.options
        self.mean = float(options.get("mean", (low + high) / 2))
        self.value = float(options.get("start", self.mean))
        self.noise = float(options.get("noise", 0.1))
        self.reversion = float(options.get("reversion", 0.05))
        self.daily_amplitude = float(options.get("daily_amplitude", 0.0))
        self.peak_hour = float(options.get("peak_hour", 15))
        self.drain_per_hour = float(options.get("drain_per_hour", 0.0))
        refill_below = options.get("refill_below")
        self.refill_below = None if refill_below is None else float(refill_below)
        self.refill_to = float(options.get("refill_to", self.value))
        self.spike_probability = float(options.get("spike_probability", 0.0))
        self.spike_size = float(options.get("spike_size", 5.0))
        self.failure_probability = float(options.get("failure_probability", 0.0))
        self.interval_seconds = interval_seconds
        self._rng = rng or random.Random()
        self._clock = clock or (lambda: datetime.now(tz))

    def daily_target(self, now: datetime) -> float:
        """Where the value is heading at this time of day."""
        hours = now.hour + now.minute / 60
        return self.mean + self.daily_amplitude * math.cos(2 * math.pi * (hours - self.peak_hour) / 24)

    def read(self) -> float:
        if self._rng.random() < self.failure_probability:
            raise SensorReadError(f"{self.key}: simulated read failure")

        if self.drain_per_hour:
            self.value -= self.drain_per_hour * self.interval_seconds / 3600
            if self.refill_below is not None and self.value < self.refill_below:
                self.value = self.refill_to
        else:
            self.value += self.reversion * (self.daily_target(self._clock()) - self.value)
        self.value += self._rng.gauss(0, self.noise)

        low, high = self.spec.valid_range
        self.value = min(max(self.value, low), high)

        reading = self.value
        if self._rng.random() < self.spike_probability:
            reading += self._rng.choice((-1, 1)) * self.spike_size
        return reading
