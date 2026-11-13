"""Build the MQTT Discovery "introduction cards" and the state messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from collector import __version__
from collector.config import Config, SensorSpec
from collector.topics import (
    CAMERA_OBJECT_ID,
    OFFLINE,
    ONLINE,
    STATUS_OBJECT_ID,
    availability_topic,
    camera_image_topic,
    camera_info_topic,
    state_topic,
)


def build_device(config: Config) -> dict[str, Any]:
    """The shared device block: makes Home Assistant group all sensors together."""
    return {
        "identifiers": [config.mqtt.device_id],
        "name": config.device.name,
        "manufacturer": config.device.manufacturer,
        "model": config.device.model,
        "sw_version": __version__,
    }


def build_discovery_payload(config: Config, spec: SensorSpec) -> dict[str, Any]:
    """Discovery config for one sensor, published retained to its discovery topic."""
    topic = state_topic(config.mqtt.base_topic, spec.key)
    payload: dict[str, Any] = {
        "name": spec.name,
        "unique_id": f"{config.mqtt.device_id}_{spec.key}",
        "state_topic": topic,
        "value_template": "{{ value_json.value }}",
        "json_attributes_topic": topic,
        "unit_of_measurement": spec.unit,
        "state_class": "measurement",
        "suggested_display_precision": spec.precision,
        # No reading for this long -> Home Assistant marks the sensor unavailable.
        "expire_after": config.stale_after_seconds,
        "availability_topic": availability_topic(config.mqtt.base_topic),
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
        "device": build_device(config),
        "origin": {"name": "attic-hydroponics-collector", "sw_version": __version__},
    }
    if spec.device_class:
        payload["device_class"] = spec.device_class
    if spec.icon:
        payload["icon"] = spec.icon
    return payload


def build_status_discovery_payload(config: Config) -> dict[str, Any]:
    """Discovery config for the "Collector" connectivity entity (online/offline).

    It reads the availability topic directly, so it shows "Disconnected" (not
    "unavailable") when the Last Will fires: something Home Assistant can alert on.
    """
    return {
        "name": "Collector",
        "unique_id": f"{config.mqtt.device_id}_{STATUS_OBJECT_ID}",
        "state_topic": availability_topic(config.mqtt.base_topic),
        "payload_on": ONLINE,
        "payload_off": OFFLINE,
        "device_class": "connectivity",
        "device": build_device(config),
        "origin": {"name": "attic-hydroponics-collector", "sw_version": __version__},
    }


def build_camera_discovery_payload(config: Config) -> dict[str, Any]:
    """Discovery config for the camera: Home Assistant shows the latest JPEG on the image topic."""
    return {
        "name": "Camera",
        "unique_id": f"{config.mqtt.device_id}_{CAMERA_OBJECT_ID}",
        "topic": camera_image_topic(config.mqtt.base_topic),
        "json_attributes_topic": camera_info_topic(config.mqtt.base_topic),
        "availability_topic": availability_topic(config.mqtt.base_topic),
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
        "device": build_device(config),
        "origin": {"name": "attic-hydroponics-collector", "sw_version": __version__},
    }


def build_state_payload(value: float, timestamp: datetime, precision: int) -> dict[str, Any]:
    """One reading: the value plus a timezone-aware ISO 8601 timestamp."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    rounded = round(float(value), precision)
    return {
        "value": int(rounded) if precision == 0 else rounded,
        "timestamp": timestamp.isoformat(timespec="seconds"),
    }
