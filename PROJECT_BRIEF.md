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
- Postponed by choice: tests beyond the sensor driver.
- Alerts (2026-11-14): phone notifications from Home Assistant for box
  temperature, humidity and VPD out of range, collector offline and a stale
  camera; thresholds editable in the app (README, "Alerts").
- Remote access is Tailscale only (personal account): the Pi, the phone, and
  since 2026-11-13 the laptop too (by choice, to SSH into the Pi in the
  attic). See docs/REAL_SENSORS.md, part 9.
- Code lives in the private repo github.com/antonio-affr/attic-hydroponics,
  committed with the personal identity only.

## Pi day (2026-11-13)

- Pi 4 Model B, 8 GB, 32 GB SD card, Raspberry Pi OS 64-bit (Debian 12).
- Sensors: two Sensirion SHT3x (temperature and humidity, both at I2C 0x44)
  behind a TCA9548A hub (0x70): port 6 inside the box, port 7 in the attic
  outside it, for comparison. Read by a driver with no extra library.
- Box VPD (kPa) is computed in Home Assistant from the box sensor (template
  sensor `sensor.box_vpd`), with the usual targets per growth stage on the
  dashboard.
- Camera Module 3 NoIR: a photo every 10 minutes, kept 7 days on the SD
  card and shown in Home Assistant (MQTT camera). Outside the box for now.
- Mosquitto, Home Assistant and the collector run on the Pi, proven end to
  end with the simulator. Next: the real sensor through the collector.

Still open:

- Cable length from the Pi into the box.
- The 4G signal at the attic spot (5 bars LTE at the desk).

Learned about the 4G modem: it is `eth1`, needs MTU 1440 for Tailscale, and
can drop without reconnecting by itself; a watchdog (every 2 minutes)
reconnects it, or restarts it. Details in docs/REAL_SENSORS.md, part 9b.
