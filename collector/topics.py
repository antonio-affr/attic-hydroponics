"""MQTT topic names. Every topic the collector uses is built here, nowhere else.

    discovery:    <discovery_prefix>/sensor/<device_id>/<sensor>/config   (retained)
                  <discovery_prefix>/binary_sensor/<device_id>/collector_status/config (retained)
    state:        <base_topic>/<sensor>/state
    availability: <base_topic>/status   ("online" / "offline", retained; Last Will = "offline")
    HA status:    <discovery_prefix>/status   (Home Assistant announces "online" after it starts)
"""

from __future__ import annotations

ONLINE = "online"
OFFLINE = "offline"
STATUS_OBJECT_ID = "collector_status"


def discovery_topic(discovery_prefix: str, device_id: str, object_id: str, component: str = "sensor") -> str:
    return f"{discovery_prefix}/{component}/{device_id}/{object_id}/config"


def state_topic(base_topic: str, sensor_key: str) -> str:
    return f"{base_topic}/{sensor_key}/state"


def availability_topic(base_topic: str) -> str:
    return f"{base_topic}/status"


def homeassistant_status_topic(discovery_prefix: str) -> str:
    return f"{discovery_prefix}/status"
