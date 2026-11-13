"""Command line. Run from the project folder:

    python -m collector run   --config config/collector.simulator.yaml [--interval 30]
    python -m collector force air_temperature 30 --minutes 5
    python -m collector clear [air_temperature]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import datetime

from collector.config import ConfigError, load_config
from collector.logging_setup import setup_logging
from collector.main import run
from collector.overrides import clear_override, write_override
from collector.validation import InvalidReading, validate_value

DEFAULT_CONFIG = "config/collector.simulator.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m collector", description="Attic hydroponics collector")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=DEFAULT_CONFIG, help=f"YAML config file (default: {DEFAULT_CONFIG})")
    common.add_argument("--env-file", default=".env", help="file with MQTT credentials (default: .env)")

    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", parents=[common], help="read sensors and publish, forever")
    p_run.add_argument("--interval", type=float, help="seconds between readings (overrides the config)")

    p_force = sub.add_parser("force", parents=[common], help="force a test value for a while")
    p_force.add_argument("sensor", help="sensor key, e.g. air_temperature")
    p_force.add_argument("value", type=float, help="value to publish instead of the real reading")
    p_force.add_argument("--minutes", type=float, default=5, help="how long to keep it (default: 5)")

    p_clear = sub.add_parser("clear", parents=[common], help="remove forced test values")
    p_clear.add_argument("sensor", nargs="?", help="sensor key (default: all)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return _cmd_run(args)
        config = load_config(args.config, env_file=args.env_file, require_credentials=False)
        now = datetime.now(config.tz)
        if args.command == "force":
            spec = config.sensor(args.sensor)
            validate_value(args.value, spec.valid_range)
            until = write_override(config.overrides_file, spec.key, args.value, args.minutes, now)
            print(f"Forcing {spec.key} = {args.value:g} {spec.unit} until {until:%H:%M:%S} (next reading picks it up).")
        else:
            removed = clear_override(config.overrides_file, args.sensor)
            print(f"Cleared: {', '.join(removed)}" if removed else "Nothing to clear.")
        return 0
    except (ConfigError, InvalidReading, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config, env_file=args.env_file)
    if args.interval is not None:
        config = config.with_interval(args.interval)
    setup_logging(config.logging, config.tz)

    stop = threading.Event()

    def _handle_signal(signum, frame) -> None:
        logging.getLogger(__name__).info("Signal %s received, stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)  # systemd stop on the Pi
    run(config, stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
