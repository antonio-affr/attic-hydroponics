# Attic Nano (wired sensor hub)

An Arduino Nano (ATmega328P, FTDI FT232R USB chip) that collects the wired
sensors and sends one JSON line per reading cycle over USB serial, 115200 baud:

```
{"device":"attic_nano_01","uptime_s":12,"light_lux":48.3,"water_temperature_c":22.25}
```

Full wiring plan: [docs/WIRING.md](../docs/WIRING.md).

Sensors so far: one BH1750 light sensor and the DS18B20 water temperature
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
./arduino-cli.exe --config-file arduino-cli.yaml lib install OneWire DallasTemperature
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
