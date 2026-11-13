# Attic Hydroponics: project brief

## Goal

Monitor (and later automate) a hydroponic grow box in the attic: see air
temperature, humidity and reservoir level from anywhere, keep their history,
and get an alert before the plants suffer.

## Physical setup

- Plants grow inside a grow box in the attic. Sensors go inside the box.
- A Raspberry Pi sits outside the box (away from humidity and water), in the
  attic, connected to the sensors by short cables.
- The attic has no home network. Its only internet is a ZTE MF833N 4G LTE USB
  modem plugged into the Pi.

## Architecture

```
Sensors (in the box) -> Python collector -> Mosquitto (MQTT) -> Home Assistant
                                                                 -> dashboard, history, alerts
Phone / laptop  --Tailscale over 4G-->  Home Assistant
```

- Everything runs on the attic Pi, in Docker Compose: Mosquitto and Home
  Assistant as containers, the collector as a systemd service.
- One Home Assistant, in the attic. If a house Home Assistant is added later,
  the attic Mosquitto bridges its readings to it over Tailscale and the attic
  Home Assistant is retired.
- No port is ever exposed to the internet. Remote access only via Tailscale.
- Mosquitto requires authentication from day one: one user for Home Assistant,
  one for the collector.

## Phases

1. **Rehearsal (Windows, Docker Desktop):** the whole chain with a Python
   simulator instead of sensors. Simulator interval: 30 seconds.
2. **Pi:** same compose file and config on the Pi; real Grove sensors for
   temperature and humidity; reservoir level stays simulated or disabled.
   Default interval: 5 minutes, configurable.
3. **Later:** Tailscale, Home Assistant phone app, real reservoir sensor,
   Xiaomi Bluetooth sensors (QBH4312GL) read by the Pi, camera snapshots,
   pH/EC/water temperature, actuators (pump, fan, lights), a possible
   whole-house Home Assistant with ESPHome devices.

## Design rules

- Portable: one project folder, Docker Compose, config files, no
  Windows-specific paths inside configs.
- The reading interval is never hardcoded. A sensor is stale after about
  2.5 times the interval.
- Timezone Europe/Lisbon everywhere; every reading carries a timezone-aware
  ISO 8601 timestamp.
- Secrets live in git-ignored files, with a committed `.example` version.
- Alert thresholds are editable in the Home Assistant UI. Alerts clear
  themselves and never repeat on every reading.

## MQTT topics

- Discovery: `homeassistant/sensor/attic_hydro/<sensor>/config` (retained)
- State: `attic/hydroponics/<sensor>/state`
- Availability: `attic/hydroponics/status` (Last Will: `offline`)

## Status (2026-11-13, end of the Windows rehearsal)

- Done: Mosquitto with auth, Home Assistant with MQTT, simulator collector with
  discovery, Last Will and stale detection, "Collector" connectivity entity,
  YAML dashboard. Versions pinned: Mosquitto 2.1.2-alpine, HA 2026.11.1.
- Postponed by choice: alerts, automated tests.
- Phone access from the work laptop is blocked by its VPN; it is done on the
  Pi instead (home Wi-Fi, then Tailscale). See docs/REAL_SENSORS.md.
- Code lives in the private repo github.com/antonio-affr/attic-hydroponics,
  committed with the personal identity only.

## Open questions for the Pi day

- Pi model, RAM, storage (SD card or SSD)?
- Exact Grove sensor models, and cable length from the Pi into the box?
- The existing Python script that reads the sensors?
- How the 4G modem shows up on the Pi?
