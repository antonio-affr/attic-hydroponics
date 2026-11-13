# Attic Hydroponics

Monitoring for a hydroponic grow box in the attic: air temperature, humidity
and reservoir level, with history and a dashboard in Home Assistant.

```
Sensors -> Python collector -> Mosquitto (MQTT) -> Home Assistant -> dashboard, history
```

- **Mosquitto** (Docker): the message broker. Password-protected, port 1883.
- **Home Assistant** (Docker): dashboard and history, at port 8123.
- **Collector** (Python): reads the sensors, validates the values and publishes
  them with MQTT Discovery, so the sensors appear in Home Assistant by themselves.

The plan and the decisions behind it are in [PROJECT_BRIEF.md](PROJECT_BRIEF.md).
Moving to the Raspberry Pi and the real sensors: [docs/REAL_SENSORS.md](docs/REAL_SENSORS.md).

## Run it on Windows (simulator)

Requirements: Docker Desktop, Python 3.11+. Commands are PowerShell, from the project folder.

```powershell
# 1. Secrets: copy the template and set two passwords
Copy-Item .env.example .env
notepad .env

# 2. Broker password file, then start Mosquitto and Home Assistant
docker compose run --rm mosquitto-passwd
docker compose up -d

# 3. Python environment for the collector
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt

# 4. Start the simulated sensors (Ctrl+C to stop)
.venv\Scripts\python -m collector run --config config/collector.simulator.yaml
```

First time only, in http://localhost:8123:

1. **Create my smart home**: account, location, analytics, Finish.
2. **Settings → Devices & services → + Add integration → MQTT**: broker `mosquitto`,
   port `1883`, username `homeassistant`, password `MQTT_HA_PASSWORD` from `.env`.

The **Attic Hydroponics** device and dashboard then appear by themselves.

## Collector commands

```powershell
.venv\Scripts\python -m collector run   --config config/collector.simulator.yaml [--interval 30]
.venv\Scripts\python -m collector force air_temperature 30 --minutes 5   # publish a test value
.venv\Scripts\python -m collector clear                                  # remove test values
```

## MQTT topics

| Purpose | Topic |
|---|---|
| Discovery (retained) | `homeassistant/sensor/attic_hydro/<sensor>/config` |
| Collector status discovery (retained) | `homeassistant/binary_sensor/attic_hydro/collector_status/config` |
| Readings | `attic/hydroponics/<sensor>/state`, JSON `{"value": 23.4, "timestamp": "2026-11-13T01:35:25+00:00"}` |
| Availability (retained, Last Will) | `attic/hydroponics/status`: `online` / `offline` |

A sensor shows **Unavailable** in Home Assistant after `interval × 2.5` without
a reading (75 s on the simulator, 12.5 min on the Pi), or immediately when the
collector disconnects.

## Project layout

```
docker-compose.yml          Mosquitto + Home Assistant (pinned versions)
.env.example                template for .env (passwords, bind addresses); .env is git-ignored
mosquitto/config/           broker settings; passwd is generated and git-ignored
homeassistant/config/       configuration.yaml, dashboards/ (runtime state is git-ignored)
collector/                  the Python collector (config, sensors/, publisher, main loop)
config/                     collector profiles: simulator (30 s) and pi (5 min)
deploy/                     systemd service for the Pi
docs/REAL_SENSORS.md        Pi and Grove sensor guide
```

## Security

- Ports are published on `127.0.0.1` (this machine only) unless `.env` says otherwise.
- Never expose 1883 or 8123 to the internet. Remote access goes through Tailscale.
- Mosquitto rejects anonymous clients; Home Assistant and the collector have separate users.

## Not done yet

- Alerts (thresholds as editable helpers, self-clearing notifications).
- Automated tests (`pytest` is installed; the tests are still to be written).
