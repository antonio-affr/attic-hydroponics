"""Camera snapshots, without a camera: a fake `rpicam-still` writes (or fails to write) the file."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from collector.camera import Camera, CameraError, capture_command
from collector.config import ConfigError, parse_config
from collector.discovery import build_camera_discovery_payload

ROOT = Path(__file__).resolve().parent.parent
LISBON = ZoneInfo("Europe/Lisbon")
TAKEN_AT = datetime(2026, 11, 13, 15, 17, 5, tzinfo=LISBON)
JPEG = b"\xff\xd8\xff\xe0fake jpeg\xff\xd9"


def load(name: str, camera: dict | None = None, tmp_path: Path | None = None):
    raw = yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))
    if camera is not None:
        raw["camera"] = camera
    if tmp_path is not None and raw.get("camera"):
        raw["camera"]["photos_dir"] = str(tmp_path / "photos")
    return parse_config(raw, env={}, require_credentials=False)


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object, bool]] = []

    def publish(self, topic: str, payload, retain: bool = False, qos: int = 0) -> bool:
        self.messages.append((topic, payload, retain))
        return True


def fake_rpicam(image: bytes = JPEG, returncode: int = 0, stderr: str = ""):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        if image:
            Path(command[command.index("-o") + 1]).write_bytes(image)
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    return run, calls


def make_camera(tmp_path, runner):
    config = load("collector.pi.yaml", tmp_path=tmp_path)
    publisher = FakePublisher()
    return Camera(config, publisher, runner=runner, clock=lambda: TAKEN_AT), publisher


def test_the_pi_config_turns_the_camera_on_and_the_simulator_leaves_it_off():
    assert load("collector.pi.yaml").camera.enabled
    assert load("collector.pi.yaml").camera.interval_seconds == 600
    assert not load("collector.simulator.yaml").camera.enabled


def test_the_command_is_the_one_tested_by_hand_on_the_pi():
    config = load("collector.pi.yaml")
    assert capture_command(config.camera, Path("x.jpg")) == [
        "rpicam-still", "-n", "--autofocus-on-capture", "--width", "1920", "--height", "1080",
        "--rotation", "180", "-o", "x.jpg",
    ]


def test_without_rotation_the_flag_is_left_out():
    config = load("collector.pi.yaml", camera={"enabled": True})
    assert "--rotation" not in capture_command(config.camera, Path("x.jpg"))


def test_a_photo_is_saved_by_date_and_published_retained(tmp_path):
    runner, calls = fake_rpicam()
    camera, publisher = make_camera(tmp_path, runner)
    assert camera.snapshot()
    saved = tmp_path / "photos" / "2026-11-13" / "151705.jpg"
    assert saved.read_bytes() == JPEG
    (image_topic, image, image_retained), (info_topic, info, _) = publisher.messages
    assert image_topic == "attic/hydroponics/camera/image" and image == JPEG and image_retained
    assert info_topic == "attic/hydroponics/camera/info"
    assert json.loads(info)["timestamp"] == "2026-11-13T15:17:05+00:00"


def test_a_failed_photo_is_logged_not_published_and_leaves_no_empty_file(tmp_path, caplog):
    runner, _ = fake_rpicam(image=b"", returncode=255, stderr="ERROR: *** no cameras available ***")
    camera, publisher = make_camera(tmp_path, runner)
    assert not camera.snapshot()
    assert publisher.messages == []
    assert "no cameras available" in caplog.text
    assert not list((tmp_path / "photos").rglob("*.jpg"))


def test_without_rpicam_still_the_error_says_so(tmp_path):
    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    camera, _ = make_camera(tmp_path, missing)
    with pytest.raises(CameraError, match="rpicam-still not found"):
        camera.take_photo()


def test_old_days_are_deleted_and_recent_ones_kept(tmp_path):
    camera, _ = make_camera(tmp_path, fake_rpicam()[0])
    photos = tmp_path / "photos"
    for day in ("2026-11-06", "2026-11-07", "2026-11-13", "not-a-date"):
        (photos / day).mkdir(parents=True)
    assert camera.prune(date(2026, 11, 13)) == 1  # keep_days 7: 7th to 13th stay
    assert sorted(p.name for p in photos.iterdir()) == ["2026-11-07", "2026-11-13", "not-a-date"]


def test_discovery_makes_a_camera_on_the_same_device():
    payload = build_camera_discovery_payload(load("collector.pi.yaml"))
    assert payload["topic"] == "attic/hydroponics/camera/image"
    assert payload["unique_id"] == "attic_hydro_camera"
    assert payload["device"]["identifiers"] == ["attic_hydro"]


@pytest.mark.parametrize(
    "camera, message",
    [
        ({"enabled": "yes"}, "true or false"),
        ({"enabled": True, "interval_seconds": 5}, "at least 10"),
        ({"enabled": True, "width": 0}, "camera.width"),
        ({"enabled": True, "keep_days": 0}, "camera.keep_days"),
        ({"enabled": True, "command": []}, "camera.command"),
        ({"enabled": True, "rotation": 90}, "0 or 180"),
    ],
)
def test_camera_config_mistakes_fail_at_startup(camera, message):
    with pytest.raises(ConfigError, match=message):
        load("collector.simulator.yaml", camera=camera)
