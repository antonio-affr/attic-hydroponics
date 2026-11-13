"""Forced test values, used to test alerts on demand.

`python -m collector force air_temperature 30 --minutes 5` writes the file;
the running collector reads it on every cycle and publishes the forced value
instead of the sensor's, until it expires. Works for real sensors too.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable overrides file %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_override(path: str | Path, sensor_key: str, value: float, minutes: float, now: datetime) -> datetime:
    """Force `sensor_key` to `value` for `minutes`. Returns the expiry time."""
    if minutes <= 0:
        raise ValueError("minutes must be greater than 0")
    path = Path(path)
    until = now + timedelta(minutes=minutes)
    data = _load(path)
    data[sensor_key] = {"value": float(value), "until": until.isoformat(timespec="seconds")}
    _save(path, data)
    return until


def clear_override(path: str | Path, sensor_key: str | None = None) -> list[str]:
    """Remove one forced value, or all of them. Returns the keys removed."""
    path = Path(path)
    data = _load(path)
    removed = list(data) if sensor_key is None else [k for k in data if k == sensor_key]
    for key in removed:
        del data[key]
    if removed:
        _save(path, data)
    return removed


def active_overrides(path: str | Path, now: datetime) -> dict[str, float]:
    """Forced values that have not expired yet, by sensor key."""
    active: dict[str, float] = {}
    for key, entry in _load(Path(path)).items():
        try:
            until = datetime.fromisoformat(entry["until"])
            value = float(entry["value"])
        except (KeyError, TypeError, ValueError):
            log.warning("Ignoring malformed override for %s: %r", key, entry)
            continue
        if now < until:
            active[key] = value
    return active
