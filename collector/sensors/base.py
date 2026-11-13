"""The shape every sensor follows: one read() that returns a number or raises."""

from __future__ import annotations

from abc import ABC, abstractmethod

from collector.config import SensorSpec


class SensorReadError(RuntimeError):
    """The sensor could not produce a reading (disconnected, timeout, bad checksum, ...)."""


class Sensor(ABC):
    def __init__(self, spec: SensorSpec) -> None:
        self.spec = spec

    @property
    def key(self) -> str:
        return self.spec.key

    @abstractmethod
    def read(self) -> float:
        """Return the current value in spec.unit, or raise SensorReadError with a clear message."""
