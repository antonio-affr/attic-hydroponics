"""Load and validate the collector configuration.

Settings come from a YAML file (committed). Secrets come from the environment,
optionally loaded from a .env file (git-ignored). Real environment variables
win over the .env file.
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

SENSOR_KEY_RE = re.compile(r"^[a-z0-9_]+$")
KNOWN_SENSOR_TYPES = frozenset({"simulated", "grove"})
PLACEHOLDER_PASSWORDS = frozenset({"", "change-me"})


class ConfigError(ValueError):
    """The configuration is missing or invalid. The message says what to fix."""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    client_id: str
    discovery_prefix: str
    base_topic: str
    device_id: str


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    model: str
    manufacturer: str = "DIY"


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: str | None = "logs/collector.log"
    max_bytes: int = 1_000_000
    backup_count: int = 5


@dataclass(frozen=True)
class SensorSpec:
    key: str
    type: str
    name: str
    unit: str
    valid_range: tuple[float, float]
    precision: int = 1
    device_class: str | None = None
    icon: str | None = None
    enabled: bool = True
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CameraConfig:
    """Optional `camera:` section. Off unless `enabled: true`."""

    enabled: bool = False
    interval_seconds: float = 600
    width: int = 1920
    height: int = 1080
    command: tuple[str, ...] = ("rpicam-still",)
    photos_dir: str = "photos"
    keep_days: int = 7


@dataclass(frozen=True)
class Config:
    timezone: str
    interval_seconds: float
    stale_after_factor: float
    mqtt: MqttConfig
    device: DeviceConfig
    logging: LoggingConfig
    overrides_file: str
    sensors: tuple[SensorSpec, ...]
    camera: CameraConfig = CameraConfig()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def stale_after_seconds(self) -> int:
        """Seconds without a reading after which a sensor counts as stale."""
        return math.ceil(self.interval_seconds * self.stale_after_factor)

    @property
    def enabled_sensors(self) -> tuple[SensorSpec, ...]:
        return tuple(s for s in self.sensors if s.enabled)

    def sensor(self, key: str) -> SensorSpec:
        for spec in self.sensors:
            if spec.key == key:
                return spec
        known = ", ".join(s.key for s in self.sensors)
        raise ConfigError(f"Unknown sensor '{key}'. Known sensors: {known}")

    def with_interval(self, interval_seconds: float) -> "Config":
        _check_interval(interval_seconds)
        return dataclasses.replace(self, interval_seconds=float(interval_seconds))


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. Blank lines and # comments are skipped."""
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config(
    path: str | Path,
    env_file: str | Path | None = ".env",
    env: Mapping[str, str] | None = None,
    require_credentials: bool = True,
) -> Config:
    """Load the YAML config at `path`, plus credentials from env_file and the environment."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file {path} is not valid YAML: {exc}") from exc

    merged_env: dict[str, str] = {}
    if env_file is not None and Path(env_file).is_file():
        merged_env.update(read_env_file(env_file))
    merged_env.update(os.environ if env is None else env)
    return parse_config(raw, merged_env, require_credentials=require_credentials)


def parse_config(
    raw: Mapping[str, Any],
    env: Mapping[str, str],
    require_credentials: bool = True,
) -> Config:
    """Build a validated Config from already-parsed YAML and an environment mapping."""
    if not isinstance(raw, Mapping):
        raise ConfigError("Config must be a YAML mapping (key: value pairs) at the top level")

    timezone = str(raw.get("timezone", "Europe/Lisbon"))
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown timezone '{timezone}'") from exc

    if "interval_seconds" not in raw:
        raise ConfigError("interval_seconds is required")
    interval = _number(raw["interval_seconds"], "interval_seconds")
    _check_interval(interval)

    stale_factor = _number(raw.get("stale_after_factor", 2.5), "stale_after_factor")
    if stale_factor < 1:
        raise ConfigError("stale_after_factor must be at least 1")

    mqtt = _parse_mqtt(raw.get("mqtt") or {}, env, require_credentials)

    device_raw = raw.get("device") or {}
    device = DeviceConfig(
        name=str(device_raw.get("name", "Attic Hydroponics")),
        model=str(device_raw.get("model", "Collector")),
        manufacturer=str(device_raw.get("manufacturer", "DIY")),
    )

    log_raw = raw.get("logging") or {}
    logging_cfg = LoggingConfig(
        level=str(log_raw.get("level", "INFO")).upper(),
        file=log_raw.get("file", "logs/collector.log"),
        max_bytes=int(log_raw.get("max_bytes", 1_000_000)),
        backup_count=int(log_raw.get("backup_count", 5)),
    )

    sensors_raw = raw.get("sensors")
    if not isinstance(sensors_raw, Mapping) or not sensors_raw:
        raise ConfigError("sensors must list at least one sensor")
    sensors = tuple(_parse_sensor(key, value) for key, value in sensors_raw.items())

    return Config(
        timezone=timezone,
        interval_seconds=interval,
        stale_after_factor=stale_factor,
        mqtt=mqtt,
        device=device,
        logging=logging_cfg,
        overrides_file=str(raw.get("overrides_file", "runtime/overrides.json")),
        sensors=sensors,
        camera=_parse_camera(raw.get("camera") or {}),
    )


def _parse_camera(raw: Any) -> CameraConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError("camera must be a mapping of settings")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError(f"camera.enabled must be true or false, got {enabled!r}")
    interval = _number(raw.get("interval_seconds", 600), "camera.interval_seconds")
    if interval < 10:
        raise ConfigError("camera.interval_seconds must be at least 10")
    whole = {}
    for name, default, minimum in (("width", 1920, 64), ("height", 1080, 64), ("keep_days", 7, 1)):
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ConfigError(f"camera.{name} must be a whole number of at least {minimum}, got {value!r}")
        whole[name] = value
    command = raw.get("command", "rpicam-still")
    command = (command,) if isinstance(command, str) else command
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ConfigError("camera.command must be a program name or a list of words")
    return CameraConfig(
        enabled=enabled,
        interval_seconds=interval,
        command=tuple(command),
        photos_dir=str(raw.get("photos_dir", "photos")),
        **whole,
    )


def _parse_mqtt(raw: Mapping[str, Any], env: Mapping[str, str], require_credentials: bool) -> MqttConfig:
    username = env.get("MQTT_COLLECTOR_USER", "")
    password = env.get("MQTT_COLLECTOR_PASSWORD", "")
    if require_credentials and (not username or password in PLACEHOLDER_PASSWORDS):
        raise ConfigError(
            "MQTT credentials missing: set MQTT_COLLECTOR_USER and MQTT_COLLECTOR_PASSWORD "
            "in .env (copy .env.example) or in the environment"
        )
    port_value = env.get("MQTT_PORT") or raw.get("port", 1883)
    try:
        port = int(port_value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"MQTT port must be a whole number, got {port_value!r}") from exc

    base_topic = str(raw.get("base_topic", "attic/hydroponics")).strip("/")
    device_id = str(raw.get("device_id", "attic_hydro"))
    if not SENSOR_KEY_RE.match(device_id):
        raise ConfigError(f"mqtt.device_id '{device_id}' may only contain a-z, 0-9 and _")
    return MqttConfig(
        host=str(env.get("MQTT_HOST") or raw.get("host", "localhost")),
        port=port,
        username=username,
        password=password,
        client_id=str(raw.get("client_id", "attic-hydro-collector")),
        discovery_prefix=str(raw.get("discovery_prefix", "homeassistant")).strip("/"),
        base_topic=base_topic,
        device_id=device_id,
    )


def _parse_sensor(key: Any, raw: Any) -> SensorSpec:
    key = str(key)
    if not SENSOR_KEY_RE.match(key):
        raise ConfigError(f"Sensor key '{key}' may only contain a-z, 0-9 and _ (it becomes an MQTT topic)")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Sensor '{key}' must be a mapping of settings")

    sensor_type = str(raw.get("type", ""))
    if sensor_type not in KNOWN_SENSOR_TYPES:
        raise ConfigError(
            f"Sensor '{key}' has unknown type '{sensor_type}'. Use one of: {', '.join(sorted(KNOWN_SENSOR_TYPES))}"
        )

    valid_range = raw.get("valid_range")
    if not isinstance(valid_range, (list, tuple)) or len(valid_range) != 2:
        raise ConfigError(f"Sensor '{key}': valid_range must be [min, max]")
    low = _number(valid_range[0], f"{key}.valid_range")
    high = _number(valid_range[1], f"{key}.valid_range")
    if low >= high:
        raise ConfigError(f"Sensor '{key}': valid_range min ({low}) must be below max ({high})")

    precision = int(raw.get("precision", 1))
    if precision < 0:
        raise ConfigError(f"Sensor '{key}': precision cannot be negative")

    options = raw.get("options") or {}
    if not isinstance(options, Mapping):
        raise ConfigError(f"Sensor '{key}': options must be a mapping")

    return SensorSpec(
        key=key,
        type=sensor_type,
        name=str(raw.get("name", key.replace("_", " ").capitalize())),
        unit=str(raw.get("unit", "")),
        valid_range=(low, high),
        precision=precision,
        device_class=raw.get("device_class"),
        icon=raw.get("icon"),
        enabled=bool(raw.get("enabled", True)),
        options=dict(options),
    )


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc
    if math.isnan(number) or math.isinf(number):
        raise ConfigError(f"{name} must be a finite number")
    return number


def _check_interval(interval: float) -> None:
    if interval <= 0:
        raise ConfigError("interval_seconds must be greater than 0")
