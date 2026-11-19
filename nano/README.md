# Attic Nano (wired sensor hub)

An Arduino Nano (ATmega328P, FTDI FT232R USB chip) that collects the wired
sensors and sends one JSON line per reading cycle over USB serial, 115200 baud:

```
{"device":"attic_nano_01","uptime_s":12,"light_1_lux":48.3,"light_2_lux":null,"light_3_lux":null,"water_temperature_c":22.25}
```

Full wiring plan: [docs/WIRING.md](../docs/WIRING.md).

Sensors so far: up to three BH1750 light sensors (A4/A5, D2/D3, D5/D6) and the DS18B20 water temperature
probe (see the top of the sketch for their wiring). Sensor power goes
through two WAGOs, one +5V and one GND; the signal wires go straight to the
shield. The DS18B20 needs its 4.7 kOhm resistor from DATA to +5V: without
it the line idles LOW and the probe is never found. A sensor that does not answer sends `null`, not 0.

## Upload the sketch (Windows)

The Arduino CLI lives in `tools/arduino/` (git-ignored, no admin rights
needed). First time only, from that folder:

```bash
curl -sSL -o cli.zip https://github.com/arduino/arduino-cli/releases/download/v1.6.0/arduino-cli_1.6.0_Windows_64bit.zip
unzip cli.zip arduino-cli.exe && rm cli.zip
# arduino-cli.yaml: directories data/downloads/user pointing inside this folder
./arduino-cli.exe --config-file arduino-cli.yaml core update-index
./arduino-cli.exe --config-file arduino-cli.yaml core install arduino:avr
./arduino-cli.exe --config-file arduino-cli.yaml lib install OneWire DallasTemperature SoftwareWire
```

Then, with the Nano plugged in (find its port with `board list`):

```bash
./arduino-cli.exe --config-file arduino-cli.yaml upload -p COM6 \
  --fqbn arduino:avr:nano:cpu=atmega328 ../../nano/attic_nano
```

(`compile` with the same `--fqbn` first.) Notes:

- The USB cable must carry data: a charge-only cable lights the LED but the
  PC sees nothing.
- Windows needs the FTDI driver (Device Manager: "FT232R USB UART" with a
  warning sign until it is installed; Windows Update provides it).
- This Nano has the new bootloader (`cpu=atmega328`). An older board may need
  `cpu=atmega328old`.
- Opening the serial port restarts the Nano, so `uptime_s` starts again.

## How the readings reach Home Assistant

The collector (`collector/sensors/nano.py`) keeps the Nano's port open, reads
its JSON lines, and publishes four sensors like any other, at the collector's
interval: `tray2grow_1_illuminance`, `tray2grow_2_illuminance`,
`grow_area_3_illuminance` (lux) and `water_temperature` (°C). Home Assistant
turns the lux into PPFD per tray (`homeassistant/config/packages/attic_tray_light.yaml`).
A `null` field or a silent Nano (30 s) is not published, so that sensor goes
Unavailable instead of showing a made-up value. The port is in the
`type: nano` sensors of `config/collector.*.yaml`: `COM6` on the laptop,
`/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_*` on the Pi.

## Moving the Nano to the Pi

1. `cd ~/attic-hydroponics && git pull`, then
   `.venv/bin/pip install -r requirements.txt` (adds pyserial).
2. `groups` must list `dialout` (it does by default on Raspberry Pi OS);
   without it the collector logs "Permission denied" on the port.
3. Plug the Nano into a Pi USB port. `ls /dev/serial/by-id/` shows
   `usb-FTDI_FT232R_USB_UART_...`.
4. `sudo systemctl restart attic-collector && docker compose restart homeassistant`.
5. Check: `journalctl -u attic-collector -n 30` shows `Nano on ...: connected`
   and, within one interval, `Published ... tray2grow_1_illuminance=...`.

The collector can start before the Nano is plugged in: it retries the port
every 5 seconds.
