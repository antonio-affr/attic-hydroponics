"""Sensors on the Arduino Nano hub (nano/attic_nano/), read over USB serial.

The Nano sends one JSON line every 2 seconds, e.g.

    {"device":"attic_nano_01","uptime_s":12,"light_1_lux":523.3,
     "light_2_lux":498.3,"light_3_lux":null,"water_temperature_c":21.4}

One background thread per serial port keeps reading those lines and remembers
the latest one. Each `type: nano` sensor in the config picks one field of it.
read() raises SensorReadError, so nothing is published and Home Assistant
marks the sensor stale, when:

- no line has arrived for max_age_seconds (Nano unplugged or frozen);
- the field is null (that sensor did not answer the Nano: loose wire);
- the field is missing (sketch and config disagree).

Opening the port restarts the Nano (that is how its USB chip works), so the
port is opened once and kept open; after an error it is reopened every few
seconds, so unplugging and replugging just works.

Config (`options:` of a `type: nano` sensor):

    port: /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_*   # or COM6 on Windows;
                                                         # a * pattern takes the first match
    field: light_1_lux        # the key in the Nano's JSON line
    max_age_seconds: 30       # optional, default 30
"""

from __future__ import annotations

import glob
import json
import logging
import threading
import time
from typing import Any, Callable, Protocol

from collector.config import ConfigError, SensorSpec
from collector.sensors.base import Sensor, SensorReadError

log = logging.getLogger(__name__)

BAUD_RATE = 115200
DEFAULT_MAX_AGE_SECONDS = 30.0
REOPEN_DELAY_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 3.0
# Opening the port restarts the Nano, which sends its first line about 2 s
# later. A reading asked for in the first seconds after start waits for it, so
# a collector restart does not lose its first round.
STARTUP_WAIT_SECONDS = 8.0


class LineSource(Protocol):
    def readline(self) -> bytes: ...

    def close(self) -> None: ...


def resolve_port(port: str) -> str:
    """A plain port is used as is; a pattern with * becomes its first match."""
    if "*" not in port:
        return port
    matches = sorted(glob.glob(port))
    if not matches:
        raise OSError(f"no serial port matches {port} (Nano unplugged?)")
    return matches[0]


def open_serial(port: str) -> LineSource:
    import serial  # pyserial; imported here so the rest of the collector runs without it

    return serial.Serial(resolve_port(port), BAUD_RATE, timeout=READ_TIMEOUT_SECONDS)


class NanoLink:
    """Keeps one serial port open and remembers the Nano's latest JSON line."""

    def __init__(
        self,
        port: str,
        opener: Callable[[str], LineSource] = open_serial,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.port = port
        self._opener = opener
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._latest_at: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._first_line = threading.Event()
        self._started_at: float | None = None

    def start(self) -> None:
        if self._thread is None:
            self._started_at = time.monotonic()
            self._thread = threading.Thread(target=self._run, name=f"nano {self.port}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def handle_line(self, raw: bytes) -> None:
        """Remember one line if it is a JSON object; ignore anything else (e.g. noise at reset)."""
        try:
            data = json.loads(raw.decode("utf-8", errors="replace").strip())
        except ValueError:
            return
        if isinstance(data, dict):
            with self._lock:
                self._latest = data
                self._latest_at = self._clock()
            self._first_line.set()

    def wait_for_first_line(self) -> None:
        """Just after start, wait (at most STARTUP_WAIT_SECONDS) for the Nano's first line."""
        if self._started_at is None:
            return
        left = STARTUP_WAIT_SECONDS - (time.monotonic() - self._started_at)
        if left > 0:
            self._first_line.wait(left)

    def latest(self) -> tuple[dict[str, Any] | None, float | None]:
        """The latest line and its age in seconds (None, None before the first one)."""
        with self._lock:
            if self._latest is None or self._latest_at is None:
                return None, None
            return self._latest, self._clock() - self._latest_at

    def _run(self) -> None:
        connected_before = False
        failing = False
        while not self._stop.is_set():
            try:
                source = self._opener(self.port)
            except Exception as exc:  # unplugged, no permission, pyserial missing, ...
                if not failing:
                    log.warning("Nano on %s: cannot open the port: %s (retrying every %ss)",
                                self.port, exc, f"{REOPEN_DELAY_SECONDS:g}")
                    failing = True
                self._stop.wait(REOPEN_DELAY_SECONDS)
                continue
            failing = False
            log.info("Nano on %s: %s", self.port, "reconnected" if connected_before else "connected")
            connected_before = True
            try:
                while not self._stop.is_set():
                    line = source.readline()
                    if line:
                        self.handle_line(line)
            except Exception as exc:
                log.warning("Nano on %s: connection lost: %s", self.port, exc)
            finally:
                try:
                    source.close()
                except Exception:
                    pass
            self._stop.wait(REOPEN_DELAY_SECONDS)


_links: dict[str, NanoLink] = {}
_links_lock = threading.Lock()


def shared_link(port: str) -> NanoLink:
    """One link per port, shared by every sensor that reads from it."""
    with _links_lock:
        link = _links.get(port)
        if link is None:
            link = NanoLink(port)
            link.start()
            _links[port] = link
        return link


class NanoSensor(Sensor):
    def __init__(self, spec: SensorSpec, link: NanoLink | None = None) -> None:
        super().__init__(spec)
        options = spec.options
        port = options.get("port")
        field = options.get("field")
        if not port or not field:
            raise ConfigError(f"Sensor '{spec.key}' (type nano) needs options 'port' and 'field'")
        self.field = str(field)
        self.max_age_seconds = float(options.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS))
        self.link = link or shared_link(str(port))

    def read(self) -> float:
        data, age = self.link.latest()
        if data is None:
            self.link.wait_for_first_line()
            data, age = self.link.latest()
        if data is None or age is None:
            raise SensorReadError(f"no data from the Nano on {self.link.port} yet")
        if age > self.max_age_seconds:
            raise SensorReadError(f"the Nano on {self.link.port} has sent nothing for {age:.0f} s")
        if self.field not in data:
            raise SensorReadError(f"the Nano does not send '{self.field}' (sketch and config disagree?)")
        value = data[self.field]
        if value is None:
            raise SensorReadError(f"'{self.field}' is null: that sensor did not answer the Nano (loose wire?)")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SensorReadError(f"'{self.field}' is not a number: {value!r}")
        return float(value)
