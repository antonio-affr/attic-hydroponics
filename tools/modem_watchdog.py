"""4G modem watchdog: bring the ZTE MF833N back online when its connection drops.

Run every 2 minutes by deploy/modem-watchdog.timer. Each run:

1. Internet through the modem (`ping -I eth1`) works -> nothing to do, no log.
2. No internet -> ask the modem its status. If it says `ppp_disconnected`,
   tell it to connect (CONNECT_NETWORK, the fix that worked on 2026-11-13)
   and test again 20 seconds later.
3. Still no internet for 3 runs in a row (about 6 minutes) -> restart the modem
   (REBOOT_DEVICE), at most once every 30 minutes so it can never loop.

The internet test goes through eth1 only, so at home the Wi-Fi cannot hide a
4G problem. When eth1 does not exist (modem unplugged, or restarting) the run
does nothing.

Standard library only: it runs with the system python3, without the venv.
State (failed runs, time of the last restart) lives in $STATE_DIRECTORY, which
systemd creates (/var/lib/modem-watchdog).

    python3 tools/modem_watchdog.py            # one run, as the timer does
    python3 tools/modem_watchdog.py --status   # just print the modem status
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MODEM_URL = "http://192.168.0.1"
INTERFACE = "eth1"
PING_TARGETS = ("1.1.1.1", "8.8.8.8")
RECONNECT_WAIT_SECONDS = 20
RESTART_AFTER_FAILED_RUNS = 3
MIN_SECONDS_BETWEEN_RESTARTS = 30 * 60
CONNECTED = "ppp_connected"

log = logging.getLogger("modem-watchdog")


# --- talking to the outside world (replaced by fakes in the tests) ------------


def interface_present(interface: str = INTERFACE) -> bool:
    return Path(f"/sys/class/net/{interface}").exists()


def internet_ok(interface: str = INTERFACE) -> bool:
    """True when any target answers a ping sent out through the modem."""
    for target in PING_TARGETS:
        result = subprocess.run(
            ["ping", "-c", "2", "-W", "3", "-I", interface, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def _modem_request(path: str, data: dict[str, str] | None = None) -> dict:
    # The modem's web pages only answer requests that look like they come from its own UI.
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(MODEM_URL + path, data=body, headers={"Referer": f"{MODEM_URL}/index.html"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def modem_status() -> dict:
    query = "ppp_status,signalbar,network_type,network_provider"
    return _modem_request(f"/goform/goform_get_cmd_process?isTest=false&multi_data=1&cmd={query}")


def modem_command(goform_id: str) -> str:
    """Send one command (CONNECT_NETWORK, REBOOT_DEVICE). Returns the modem's "result"."""
    answer = _modem_request("/goform/goform_set_cmd_process", {"isTest": "false", "goformId": goform_id})
    return str(answer.get("result", answer))


# --- the decision -------------------------------------------------------------


@dataclass
class State:
    failed_runs: int = 0
    last_restart: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(failed_runs=int(raw["failed_runs"]), last_restart=float(raw["last_restart"]))
        except (OSError, ValueError, KeyError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"failed_runs": self.failed_runs, "last_restart": self.last_restart}), encoding="utf-8")


@dataclass
class Watchdog:
    interface_present: Callable[[], bool] = interface_present
    internet_ok: Callable[[], bool] = internet_ok
    modem_status: Callable[[], dict] = modem_status
    modem_command: Callable[[str], str] = modem_command
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    actions: list[str] = field(default_factory=list)  # what this run did, for the tests

    def run_once(self, state: State) -> State:
        if not self.interface_present():
            log.info("%s is missing (modem unplugged or restarting): nothing to do", INTERFACE)
            return state
        if self.internet_ok():
            if state.failed_runs:
                log.info("Internet through the modem is back")
            return State(failed_runs=0, last_restart=state.last_restart)

        try:
            status = self.modem_status()
        except Exception as exc:  # the modem's web page does not answer: nothing we can send it
            log.warning("No internet, and the modem's web page does not answer (%s)", exc)
            return State(failed_runs=state.failed_runs + 1, last_restart=state.last_restart)

        ppp = status.get("ppp_status", "?")
        log.warning(
            "No internet through %s; modem says %s, signal %s/5, %s %s",
            INTERFACE, ppp, status.get("signalbar", "?"), status.get("network_provider", "?"), status.get("network_type", "?"),
        )
        if ppp != CONNECTED:
            result = self._command("CONNECT_NETWORK")
            log.info("Asked the modem to connect: %s", result)
            self.sleep(RECONNECT_WAIT_SECONDS)
            if self.internet_ok():
                log.info("Reconnected: internet through the modem works again")
                return State(failed_runs=0, last_restart=state.last_restart)

        failed = state.failed_runs + 1
        since_restart = self.now() - state.last_restart
        if failed >= RESTART_AFTER_FAILED_RUNS and since_restart >= MIN_SECONDS_BETWEEN_RESTARTS:
            result = self._command("REBOOT_DEVICE")
            log.warning("Still no internet after %d runs: restarting the modem (%s)", failed, result)
            return State(failed_runs=0, last_restart=self.now())
        log.warning("Still no internet (run %d of %d before a modem restart)", failed, RESTART_AFTER_FAILED_RUNS)
        return State(failed_runs=failed, last_restart=state.last_restart)

    def _command(self, goform_id: str) -> str:
        self.actions.append(goform_id)
        try:
            return self.modem_command(goform_id)
        except Exception as exc:
            return f"no answer ({exc})"


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    if "--status" in argv:
        print(json.dumps(modem_status()))
        return 0
    state_file = Path(os.environ.get("STATE_DIRECTORY", tempfile.gettempdir())) / "modem-watchdog.json"
    state = Watchdog().run_once(State.load(state_file))
    state.save(state_file)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
