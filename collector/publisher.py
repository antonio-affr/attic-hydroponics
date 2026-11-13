"""MQTT connection: Last Will, birth message, automatic reconnect, safe publish.

Uses paho-mqtt 2.x with callback API version 2. The network loop runs in a
background thread (loop_start), which also reconnects automatically with a
growing delay (1 s up to 60 s). publish() never raises: when the broker is
unreachable it logs and returns False, so the main loop keeps running.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from collector.config import MqttConfig
from collector.topics import OFFLINE, ONLINE, availability_topic, homeassistant_status_topic

log = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(self, config: MqttConfig, on_connected: Callable[[], None] | None = None) -> None:
        self._config = config
        self.on_connected = on_connected
        self._connected = threading.Event()  # TCP/MQTT session is up: publishing is allowed
        self._ready = threading.Event()  # ...and "online" + discovery have been sent
        self._availability = availability_topic(config.base_topic)
        self._ha_status = homeassistant_status_topic(config.discovery_prefix)

        client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=config.client_id)
        client.username_pw_set(config.username, config.password)
        # If we vanish without saying goodbye, the broker announces "offline" for us.
        client.will_set(self._availability, OFFLINE, qos=1, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.enable_logger(logging.getLogger("paho"))
        self._client = client

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        log.info("Connecting to MQTT broker %s:%s as %s", self._config.host, self._config.port, self._config.username)
        self._client.connect_async(self._config.host, self._config.port, keepalive=60)
        self._client.loop_start()

    def wait_until_connected(self, timeout: float) -> bool:
        """Wait until connected AND discovery has been sent, so no reading beats its discovery."""
        return self._ready.wait(timeout)

    def publish(self, topic: str, payload: str | bytes, retain: bool = False, qos: int = 0) -> bool:
        if not self.connected:
            log.warning("Not connected to the broker, skipping publish to %s", topic)
            return False
        try:
            info = self._client.publish(topic, payload, qos=qos, retain=retain)
        except Exception:  # never let a publish problem stop the main loop
            log.exception("Publish to %s failed", topic)
            return False
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Publish to %s failed: %s", topic, mqtt.error_string(info.rc))
            return False
        return True

    def stop(self) -> None:
        """Say "offline" on purpose (a clean exit does not trigger the Last Will), then disconnect."""
        if self.connected:
            info = self._client.publish(self._availability, OFFLINE, qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=5)
            except (RuntimeError, ValueError):
                pass
        self._client.disconnect()
        self._client.loop_stop()
        log.info("Disconnected from the MQTT broker")

    # paho callbacks (run in paho's network thread) ------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("Broker refused the connection: %s (check MQTT_COLLECTOR_USER / PASSWORD)", reason_code)
            return
        log.info("Connected to the MQTT broker")
        self._connected.set()
        client.publish(self._availability, ONLINE, qos=1, retain=True)
        # When Home Assistant restarts it announces "online"; we then resend discovery.
        client.subscribe(self._ha_status, qos=1)
        self._notify_connected()
        self._ready.set()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        was_connected = self._connected.is_set()
        self._connected.clear()
        self._ready.clear()
        if was_connected:
            log.warning("Disconnected from the broker (%s), reconnecting automatically", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        if message.topic == self._ha_status and message.payload.decode(errors="replace") == ONLINE:
            log.info("Home Assistant came online, resending discovery")
            self._notify_connected()

    def _notify_connected(self) -> None:
        if self.on_connected is None:
            return
        try:
            self.on_connected()
        except Exception:
            log.exception("Error while handling (re)connection")
