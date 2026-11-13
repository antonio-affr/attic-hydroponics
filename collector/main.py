"""The collector loop: read every sensor, validate, publish, wait, repeat."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Iterable

from collector.config import Config
from collector.discovery import build_discovery_payload, build_state_payload, build_status_discovery_payload
from collector.overrides import active_overrides
from collector.publisher import MqttPublisher
from collector.sensors import Sensor, SensorReadError, build_sensor
from collector.topics import STATUS_OBJECT_ID, discovery_topic, state_topic
from collector.validation import InvalidReading, validate_value

log = logging.getLogger(__name__)

# After (re)sending discovery, wait this long and then resend the latest readings.
# Home Assistant needs a moment to create the sensors before it listens for them;
# without this, a brand-new or just-restarted Home Assistant shows "Unavailable"
# until the next reading, which on the Pi is up to 5 minutes away.
RESEND_DELAY_SECONDS = 3.0


class Collector:
    def __init__(
        self,
        config: Config,
        sensors: Iterable[Sensor],
        publisher: MqttPublisher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.sensors = list(sensors)
        self.publisher = publisher
        self._clock = clock or (lambda: datetime.now(config.tz))
        self._latest: dict[str, tuple[str, float]] = {}  # state topic -> (JSON payload, monotonic time read)
        self._latest_lock = threading.Lock()
        self._resend_timer: threading.Timer | None = None

    def publish_discovery(self) -> None:
        mqtt_cfg = self.config.mqtt
        for sensor in self.sensors:
            topic = discovery_topic(mqtt_cfg.discovery_prefix, mqtt_cfg.device_id, sensor.key)
            payload = build_discovery_payload(self.config, sensor.spec)
            self.publisher.publish(topic, json.dumps(payload), retain=True, qos=1)
        status_topic = discovery_topic(mqtt_cfg.discovery_prefix, mqtt_cfg.device_id, STATUS_OBJECT_ID, "binary_sensor")
        self.publisher.publish(status_topic, json.dumps(build_status_discovery_payload(self.config)), retain=True, qos=1)
        log.info("Published discovery for %d sensors + collector status", len(self.sensors))
        self._schedule_resend()

    def _schedule_resend(self) -> None:
        if self._resend_timer is not None:
            self._resend_timer.cancel()
        timer = threading.Timer(RESEND_DELAY_SECONDS, self.resend_latest)
        timer.daemon = True
        timer.start()
        self._resend_timer = timer

    def resend_latest(self) -> int:
        """Publish again the latest valid reading of each sensor, if it is not stale yet."""
        now = time.monotonic()
        with self._latest_lock:
            latest = list(self._latest.items())
        resent = 0
        for topic, (payload, read_at) in latest:
            if now - read_at <= self.config.stale_after_seconds and self.publisher.publish(topic, payload):
                resent += 1
        if resent:
            log.info("Resent the latest %d readings so Home Assistant shows them straight away", resent)
        return resent

    def close(self) -> None:
        if self._resend_timer is not None:
            self._resend_timer.cancel()

    def collect_once(self) -> dict[str, float]:
        """Read, validate and publish every sensor once. Returns what was published."""
        now = self._clock()
        forced = active_overrides(self.config.overrides_file, now)
        published: dict[str, float] = {}
        for sensor in self.sensors:
            key = sensor.key
            try:
                if key in forced:
                    raw = forced[key]
                    log.warning("%s: using FORCED test value %s", key, raw)
                else:
                    raw = sensor.read()
                value = validate_value(raw, sensor.spec.valid_range)
            except (SensorReadError, InvalidReading) as exc:
                log.warning("%s: reading rejected, not published: %s", key, exc)
                continue
            except Exception:
                log.exception("%s: unexpected error while reading", key)
                continue

            payload = build_state_payload(value, now, sensor.spec.precision)
            topic = state_topic(self.config.mqtt.base_topic, key)
            message = json.dumps(payload)
            with self._latest_lock:
                self._latest[topic] = (message, time.monotonic())
            if self.publisher.publish(topic, message):
                published[key] = payload["value"]
        if published:
            log.info("Published %s", ", ".join(f"{k}={v}" for k, v in published.items()))
        return published


def run_forever(collector: Collector, interval_seconds: float, stop: threading.Event) -> None:
    """Call collect_once every interval until `stop` is set. Never raises."""
    next_run = time.monotonic()
    while not stop.is_set():
        try:
            collector.collect_once()
        except Exception:
            log.exception("Unexpected error in collection cycle")
        next_run += interval_seconds
        if next_run < time.monotonic():  # fell behind (e.g. laptop asleep): restart the schedule
            next_run = time.monotonic()
        # Wait in short slices so Ctrl+C is handled promptly on Windows too.
        while not stop.is_set():
            remaining = next_run - time.monotonic()
            if remaining <= 0:
                break
            stop.wait(min(remaining, 1.0))


def run(config: Config, stop: threading.Event) -> None:
    sensors = [build_sensor(spec, config.interval_seconds, config.tz) for spec in config.enabled_sensors]
    publisher = MqttPublisher(config.mqtt)
    collector = Collector(config, sensors, publisher)
    publisher.on_connected = collector.publish_discovery

    log.info(
        "Starting collector: %d sensors (%s), every %ss, stale after %ss",
        len(sensors),
        ", ".join(s.key for s in sensors),
        f"{config.interval_seconds:g}",
        config.stale_after_seconds,
    )
    publisher.start()
    if not publisher.wait_until_connected(timeout=15):
        log.warning("Broker not reachable yet; will keep retrying in the background")
    try:
        run_forever(collector, config.interval_seconds, stop)
    finally:
        collector.close()
        publisher.stop()
        log.info("Collector stopped")
