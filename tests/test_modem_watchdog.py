"""The 4G watchdog's decisions, with a fake modem and a fake internet."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("modem_watchdog", Path(__file__).resolve().parent.parent / "tools" / "modem_watchdog.py")
mw = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mw  # dataclasses look the module up by name
_spec.loader.exec_module(mw)

NOW = 1_800_000_000.0


class FakeModem:
    """ppp_status flips to connected after CONNECT_NETWORK when `reconnect_works`."""

    def __init__(self, ppp: str = "ppp_disconnected", reconnect_works: bool = True, answers: bool = True) -> None:
        self.ppp, self.reconnect_works, self.answers = ppp, reconnect_works, answers
        self.commands: list[str] = []

    def status(self) -> dict:
        if not self.answers:
            raise OSError("timed out")
        return {"ppp_status": self.ppp, "signalbar": "5", "network_type": "LTE", "network_provider": "DIGI PT"}

    def command(self, goform_id: str) -> str:
        self.commands.append(goform_id)
        if goform_id == "CONNECT_NETWORK" and self.reconnect_works:
            self.ppp = "ppp_connected"
        return "success"

    def internet(self) -> bool:
        return self.ppp == "ppp_connected" and self.reconnect_works


def watchdog(modem: FakeModem, present: bool = True, internet=None) -> mw.Watchdog:
    return mw.Watchdog(
        interface_present=lambda: present,
        internet_ok=internet or modem.internet,
        modem_status=modem.status,
        modem_command=modem.command,
        sleep=lambda _: None,
        now=lambda: NOW,
    )


def test_internet_ok_does_nothing():
    modem = FakeModem(ppp="ppp_connected")
    state = watchdog(modem).run_once(mw.State(failed_runs=2))
    assert modem.commands == [] and state.failed_runs == 0


def test_a_dropped_connection_is_reconnected_like_on_2026_11_13():
    modem = FakeModem()
    state = watchdog(modem).run_once(mw.State())
    assert modem.commands == ["CONNECT_NETWORK"]
    assert state.failed_runs == 0


def test_the_modem_is_restarted_after_three_failed_runs_not_before():
    modem = FakeModem(reconnect_works=False)
    state = mw.State()
    for run in (1, 2):
        state = watchdog(modem).run_once(state)
        assert state.failed_runs == run and "REBOOT_DEVICE" not in modem.commands
    state = watchdog(modem).run_once(state)
    assert modem.commands[-1] == "REBOOT_DEVICE"
    assert state == mw.State(failed_runs=0, last_restart=NOW)


def test_never_more_than_one_restart_every_30_minutes():
    modem = FakeModem(reconnect_works=False)
    state = mw.State(failed_runs=5, last_restart=NOW - 10 * 60)
    state = watchdog(modem).run_once(state)
    assert "REBOOT_DEVICE" not in modem.commands and state.failed_runs == 6


def test_connected_but_no_internet_skips_the_reconnect_and_counts_toward_a_restart():
    modem = FakeModem(ppp="ppp_connected")
    state = watchdog(modem, internet=lambda: False).run_once(mw.State())
    assert modem.commands == [] and state.failed_runs == 1


def test_no_modem_plugged_in_means_nothing_to_do():
    modem = FakeModem()
    state = watchdog(modem, present=False).run_once(mw.State(failed_runs=1))
    assert modem.commands == [] and state.failed_runs == 1


def test_a_silent_modem_counts_as_a_failed_run():
    modem = FakeModem(answers=False)
    state = watchdog(modem, internet=lambda: False).run_once(mw.State())
    assert modem.commands == [] and state.failed_runs == 1


@pytest.mark.parametrize("content", ["", "not json", '{"failed_runs": "x"}'])
def test_a_broken_state_file_starts_fresh(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    assert mw.State.load(path) == mw.State()


def test_state_survives_a_save_and_load(tmp_path):
    path = tmp_path / "state.json"
    mw.State(failed_runs=2, last_restart=NOW).save(path)
    assert mw.State.load(path) == mw.State(failed_runs=2, last_restart=NOW)
