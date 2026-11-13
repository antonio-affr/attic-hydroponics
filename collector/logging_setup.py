"""Logging to the console and to a rotating file, with timezone-aware timestamps."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from collector.config import LoggingConfig


class _TzFormatter(logging.Formatter):
    def __init__(self, fmt: str, tz: ZoneInfo) -> None:
        super().__init__(fmt)
        self._tz = tz

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, self._tz).isoformat(timespec="seconds")


def setup_logging(config: LoggingConfig, tz: ZoneInfo) -> None:
    formatter = _TzFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", tz)
    root = logging.getLogger()
    root.setLevel(config.level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if config.file:
        path = Path(config.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=config.max_bytes, backupCount=config.backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # paho is chatty at DEBUG; keep its warnings and errors only.
    logging.getLogger("paho").setLevel(logging.WARNING)
