"""Reject readings that cannot be physically true before they are published."""

from __future__ import annotations

import math
from numbers import Real


class InvalidReading(ValueError):
    """A reading that must not be published. The message says why."""


def validate_value(value: object, valid_range: tuple[float, float]) -> float:
    """Return the value as float if it is a finite number inside valid_range, else raise."""
    if value is None:
        raise InvalidReading("no value (sensor returned None)")
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidReading(f"not a number: {value!r}")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise InvalidReading(f"not a finite number: {number}")
    low, high = valid_range
    if not low <= number <= high:
        raise InvalidReading(f"{number} is outside the valid range [{low}, {high}]")
    return number
