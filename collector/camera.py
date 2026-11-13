"""Camera snapshots: a photo every few minutes, kept on disk and shown in Home Assistant.

    rpicam-still -n --autofocus-on-capture --width W --height H -o photos/<date>/<time>.jpg

Each photo is saved under `photos_dir/YYYY-MM-DD/HHMMSS.jpg` (folders older than
`keep_days` are deleted), then published retained to `<base_topic>/camera/image`.
Home Assistant's MQTT camera shows the latest one; because it is retained, a
restarted Home Assistant gets it back at once.

The photo travels from the camera to Home Assistant inside the Pi. Mobile data
is only used when someone opens the dashboard.

A failed photo is logged and skipped; the sensors keep running regardless.

Config (`camera:` in config/collector.pi.yaml):

    enabled: true
    interval_seconds: 600     # one photo every 10 minutes
    width: 1920
    height: 1080
    keep_days: 7              # about 45 MB a day at 1920x1080
    photos_dir: photos
    rotation: 180             # 0 or 180: for a camera mounted upside down
    command: rpicam-still     # or a list of words, e.g. a test script
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from collector.config import CameraConfig, Config
from collector.discovery import build_camera_discovery_payload
from collector.topics import CAMERA_OBJECT_ID, camera_image_topic, camera_info_topic, discovery_topic

log = logging.getLogger(__name__)

CAPTURE_TIMEOUT_SECONDS = 60  # autofocus plus capture takes about 5 s


class CameraError(RuntimeError):
    """The photo could not be taken. The message says why."""


def capture_command(camera: CameraConfig, output: Path) -> list[str]:
    command = [
        *camera.command,
        "-n",  # no preview window: there is no screen
        "--autofocus-on-capture",
        "--width",
        str(camera.width),
        "--height",
        str(camera.height),
    ]
    if camera.rotation:
        command += ["--rotation", str(camera.rotation)]  # the sensor flips the picture: no quality loss
    return command + ["-o", str(output)]


class Camera:
    def __init__(
        self,
        config: Config,
        publisher,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.camera = config.camera
        self.publisher = publisher
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(config.tz))
        self._photos = Path(self.camera.photos_dir)

    def publish_discovery(self) -> None:
        mqtt_cfg = self.config.mqtt
        topic = discovery_topic(mqtt_cfg.discovery_prefix, mqtt_cfg.device_id, CAMERA_OBJECT_ID, "camera")
        self.publisher.publish(topic, json.dumps(build_camera_discovery_payload(self.config)), retain=True, qos=1)

    def take_photo(self) -> tuple[Path, datetime]:
        """Take one photo and save it. Raises CameraError on any problem."""
        now = self._clock()
        output = self._photos / now.strftime("%Y-%m-%d") / now.strftime("%H%M%S.jpg")
        output.parent.mkdir(parents=True, exist_ok=True)
        command = capture_command(self.camera, output)
        try:
            result = self._runner(command, capture_output=True, text=True, timeout=CAPTURE_TIMEOUT_SECONDS, check=False)
        except FileNotFoundError as exc:
            raise CameraError(f"{command[0]} not found: is this the Pi, with rpicam-apps installed?") from exc
        except subprocess.TimeoutExpired as exc:
            raise CameraError(f"{command[0]} took more than {CAPTURE_TIMEOUT_SECONDS} s") from exc
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            lines = (result.stderr or "").strip().splitlines()
            reason = lines[-1] if lines else f"exit code {result.returncode}"
            output.unlink(missing_ok=True)
            raise CameraError(f"no photo taken: {reason} (is the ribbon cable seated?)")
        return output, now

    def snapshot(self) -> bool:
        """Take, save and publish one photo. Never raises; returns True when published."""
        try:
            path, taken_at = self.take_photo()
        except CameraError as exc:
            log.warning("Camera: %s", exc)
            return False
        except Exception:
            log.exception("Camera: unexpected error")
            return False
        image = path.read_bytes()
        base = self.config.mqtt.base_topic
        info = {"timestamp": taken_at.isoformat(timespec="seconds"), "size_kb": round(len(image) / 1024), "file": str(path)}
        published = self.publisher.publish(camera_image_topic(base), image, retain=True, qos=1)
        self.publisher.publish(camera_info_topic(base), json.dumps(info), retain=True, qos=1)
        log.info("Camera: photo %s (%d KB)%s", path, info["size_kb"], "" if published else ", saved but not published")
        self.prune(taken_at.date())
        return published

    def prune(self, today: date) -> int:
        """Delete the date folders older than keep_days. Returns how many were deleted."""
        oldest_kept = today - timedelta(days=self.camera.keep_days - 1)
        deleted = 0
        if not self._photos.is_dir():
            return 0
        for folder in self._photos.iterdir():
            try:
                day = date.fromisoformat(folder.name)
            except ValueError:
                continue  # not one of ours: leave it alone
            if folder.is_dir() and day < oldest_kept:
                shutil.rmtree(folder)
                deleted += 1
        if deleted:
            log.info("Camera: deleted %d day(s) of old photos", deleted)
        return deleted
