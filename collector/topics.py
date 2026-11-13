"""MQTT topic names. Every topic the collector uses is built here, nowhere else.

    discovery:    <discovery_prefix>/sensor/<device_id>/<sensor>/config   (retained)
                  <discovery_prefix>/binary_sensor/<device_id>/collector_status/config (retained)
    state:        <base_topic>/<sensor>/state
    camera:       <discovery_prefix>/camera/<device_id>/camera/config (retained)
                  <base_topic>/camera/image  (JPEG bytes, retained)
                  <base_topic>/camera/info   (JSON: timestamp and size of that photo, retained)
    availability: <base_topic>/status   ("online" / "offline", retained; Last Will = "offline")
    HA status:    <discovery_prefix>/status   (Home Assistant announces "online" after it starts)
"""

from __future__ import annotations

ONLINE = "online"
OFFLINE = "offline"
STATUS_OBJECT_ID = "collector_status"
CAMERA_OBJECT_ID = "camera"


def discovery_topic(discovery_prefix: str, device_id: str, object_id: str, component: str = "sensor") -> str:
    return f"{discovery_prefix}/{component}/{device_id}/{object_id}/config"


def state_topic(base_topic: str, sensor_key: str) -> str:
    return f"{base_topic}/{sensor_key}/state"


def camera_image_topic(base_topic: str) -> str:
    return f"{base_topic}/camera/image"


def camera_info_topic(base_topic: str) -> str:
    return f"{base_topic}/camera/info"


def availability_topic(base_topic: str) -> str:
    return f"{base_topic}/status"


def homeassistant_status_topic(discovery_prefix: str) -> str:
    return f"{discovery_prefix}/status"
