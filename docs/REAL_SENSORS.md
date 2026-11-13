# Moving to the Raspberry Pi and the real Grove sensors

This guide takes the system rehearsed on Windows and runs it on the Pi in the
attic. Do one part at a time; each part ends with a check.

What changes compared with Windows:

- The same `docker-compose.yml` runs Mosquitto and Home Assistant on the Pi.
- The collector runs directly on the Pi (not in Docker), as a systemd service,
  with `config/collector.pi.yaml` (interval 5 minutes, stale after 12.5 minutes).
- Air temperature and humidity come from the Grove sensor; the reservoir level
  stays simulated until there is hardware for it.
- Tonight's history stays on the laptop. The Pi starts with empty history.

All commands below run **on the Pi** (bash), unless marked otherwise.

**This Pi (checked 2026-11-13):** Raspberry Pi 4 Model B, 8 GB, 32 GB SD card,
Raspberry Pi OS 64-bit (Debian 12), user `tonypi`, hostname `tony-rpi`,
Python 3.11. I2C was already enabled and the timezone already Europe/Lisbon.
Sensors: two Sensirion **SHT3x** (both at I2C address `0x44`) behind a
**TCA9548A** 8-channel hub at `0x70` on bus 1: hub port 6 inside the box, port 7
in the attic outside it.

**Its address on the home Wi-Fi is not fixed.** It was `192.168.1.70`, then
`192.168.1.198`. On the Pi, `hostname -I` shows the current one; from the
laptop, the router's device list does too. A DHCP reservation in the router
fixes it; on 4G only the Tailscale name matters (part 9).

---

## 0. Questions to answer first

| Question | Why it matters |
|---|---|
| Pi model and RAM? (`cat /proc/device-tree/model`, `free -h`) | Home Assistant needs a Pi 4 (2 GB+) or Pi 5. |
| Storage: SD card or SSD? | HA writes its database constantly; an SSD lasts much longer. |
| OS 64-bit? (`uname -m` must say `aarch64`) | The Docker images need 64-bit. |
| Exact Grove sensor model(s)? | Decides the driver and the I2C address. |
| Grove HAT / Base Hat model? | Decides the connector and the library. |
| Cable length from the Pi into the grow box? | Grove/I2C is reliable up to about 1 m. |
| Where is the existing Python script that reads the sensor? | It becomes `collector/sensors/grove.py`. |
| How does the ZTE MF833N show up? (`ip -br addr`) | Usually as a normal network interface (`usb0`/`eth1`). |
| User name on the Pi? (`whoami`) | The service file assumes `tonypi`. |

---

## 1. Base system

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv i2c-tools
sudo timedatectl set-timezone Europe/Lisbon
sudo raspi-config nonint do_i2c 0      # enable I2C (0 = on)
```

**Check:** `timedatectl` shows `Europe/Lisbon`; `ls /dev/i2c-*` lists at least one device.

## 2. Docker

```bash
curl -fsSL https://get.docker.com | sh   # official Docker install script
sudo usermod -aG docker $USER            # use docker without sudo
# log out and back in, then:
docker run --rm hello-world
docker compose version
```

**Check:** "Hello from Docker!" and a Compose version, as on Windows (Step 1).
Docker starts at boot by default (`systemctl is-enabled docker` → `enabled`).

## 3. Get the project (private repo → deploy key)

The repo is private, so the Pi needs its own read-only key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/attic_deploy -N "" -C "attic-pi deploy key"
cat ~/.ssh/attic_deploy.pub
```

On GitHub: **antonio-affr/attic-hydroponics → Settings → Deploy keys → Add deploy key**.
Paste the key, title `attic-pi`, leave **Allow write access** unticked.

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-attic
  HostName github.com
  User git
  IdentityFile ~/.ssh/attic_deploy
  IdentitiesOnly yes
EOF
git clone git@github-attic:antonio-affr/attic-hydroponics.git ~/attic-hydroponics
cd ~/attic-hydroponics
```

**Check:** `git log --oneline` shows the same commits as on GitHub.
Later updates: `git pull`.

## 4. Secrets and broker

```bash
cp .env.example .env
nano .env
```

In `.env`:

- set both `MQTT_*_PASSWORD` to new random values (`openssl rand -base64 18`);
- set `HA_BIND_ADDRESS=0.0.0.0` (see "Why 0.0.0.0 is safe here" below);
- keep `MQTT_BIND_ADDRESS=127.0.0.1` (the collector runs on the Pi itself).

```bash
docker compose run --rm mosquitto-passwd
docker compose up -d
docker compose ps
```

The helper also hands `mosquitto/data` and `mosquitto/log` to the broker's user
(uid 1883). On the Pi a fresh clone leaves them to `tonypi`, and Mosquitto then
stops with `Unable to open log file ... for writing`. If you ever see that
error, run the helper again and `docker compose restart mosquitto`.

**Check:** both services `Up`; `docker compose logs mosquitto` ends with
`mosquitto version ... running`.

**Why 0.0.0.0 is safe here:** the Pi's only network is the 4G modem, whose
mobile network refuses incoming connections from the internet. 0.0.0.0 lets
Home Assistant answer on the home Wi-Fi during setup and on Tailscale later.
Never forward port 8123 or 1883 on any router.

## 5. Home Assistant first-time setup

Same clicks as on Windows, from a laptop or phone on the same network as the Pi:

1. Open `http://<pi-address>:8123` (at home: `http://192.168.1.198:8123`).
   This is a new Home Assistant: the account, the MQTT connection and the
   history are not in git (they hold secrets), so they are created again here.
   The name, units and dashboard come from git.
2. **Create my smart home** → account → location → analytics → Finish.
3. **Settings → Devices & services → + Add integration → MQTT**
   - Broker `mosquitto`, port `1883`, username `homeassistant`,
     password = `MQTT_HA_PASSWORD` from the Pi's `.env`
     (`grep MQTT_HA_PASSWORD .env`).

**Check:** `docker compose logs mosquitto | grep homeassistant` shows a client
connected as `u'homeassistant'`.

## 6. Collector with the simulator first (proves the chain on the Pi)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m collector run --config config/collector.simulator.yaml
```

**Check:** the **Attic Hydroponics** device appears in HA with three sensors,
and the **Attic Hydroponics** dashboard in the sidebar fills in. Stop with Ctrl+C.

## 7. The real Grove sensor (SHT3x)

The driver is `collector/sensors/grove.py`. It needs no extra library: it talks
to `/dev/i2c-1` directly, the same way as the manual test below. Temperature and
humidity come from one chip, read once per cycle.

1. Find the sensor on the I2C bus: `i2cdetect -y 1`. The SHT3x shows as `44`
   (or `45` if the board's address pad was changed; then set `address: 0x45`
   in both sensors of `config/collector.pi.yaml`).
2. Optional manual reading, which also tells the chip family apart:

   ```bash
   i2ctransfer -y 1 w2@0x44 0x24 0x00 && sleep 0.05 && i2ctransfer -y 1 r6@0x44   # SHT3x: 6 bytes
   i2ctransfer -y 1 w1@0x44 0xFD && sleep 0.05 && i2ctransfer -y 1 r6@0x44        # SHT4x: 6 bytes
   ```

   On 2026-11-13 the first returned `0x63 0x39 0xb4 0xaf 0xdb 0x09` (22.8 °C,
   68.7 %, valid checksums) and the second an I/O error: an SHT3x. Those bytes
   are now a test (`tests/test_grove.py`).
3. The user must be in the `i2c` group (`groups` shows it; it is by default
   on Raspberry Pi OS).
4. Try it with a short interval:

```bash
.venv/bin/python -m collector run --config config/collector.pi.yaml --interval 30
```

**Check:** the log shows `Published air_temperature=..., humidity=...` with
believable values; breathe on the sensor and humidity rises. A missing or
broken sensor shows `reading rejected, not published: SHT3x ... did not answer`
and HA marks it stale after 2.5 × the interval. That is the intended behaviour.

The device in Home Assistant changes from "Simulator" to the Pi model by
itself. The entities and the dashboard stay.

### Two sensors through the TCA9548A hub

Two identical SHT3x boards both answer at `0x44`, so they cannot share the bus
directly. The TCA9548A hub gives each one its own channel: it keeps all
channels closed at power-up, and the driver opens the right one (one byte,
`1 << channel`, sent to the hub at `0x70`) just before each measurement.

Wiring: Pi → hub input; each sensor → one numbered hub port (`SD6/SC6` is
port 6). Shut the Pi down before rewiring.

1. `i2cdetect -y 1` now shows only `70` (the sensors are hidden behind the hub).
2. Find which port each sensor is on (opens each channel, lists what answers,
   then closes them all):

   ```bash
   for ch in 0 1 2 3 4 5 6 7; do i2cset -y 1 0x70 $((1<<ch)); printf "channel %s: " $ch; i2cdetect -y 1 | tail -n +2 | cut -c5- | tr -s ' ' '\n' | grep -vE '^(--|70|)$' | tr '\n' ' '; echo; done; i2cset -y 1 0x70 0x00
   ```

   On 2026-11-13: `channel 6: 44` and `channel 7: 44`.
3. Each sensor in `config/collector.pi.yaml` names its port with
   `mux_channel:` (and `mux_address: 0x70`). Port 6 is the box
   (`air_temperature`, `humidity`), port 7 the attic (`attic_temperature`,
   `attic_humidity`). Moving a sensor to another port means changing
   `mux_channel` in its two entries and restarting the service.

**Check:** the log shows all four values each cycle; breathing on the box
sensor raises `humidity` but not `attic_humidity`.

## 7b. The camera (Camera Module 3 NoIR)

The collector takes a photo every 10 minutes (`camera:` in
`config/collector.pi.yaml`), keeps it in `photos/<date>/` for 7 days (about
45 MB a day) and sends it to Home Assistant, which shows it on the dashboard.
The photo goes from the camera to Home Assistant inside the Pi; mobile data is
used only when someone opens the dashboard.

1. Shut the Pi down, then plug the ribbon into the **CAMERA** connector (between
   the micro-HDMI ports and the audio jack): lift the latch, silver contacts
   facing the HDMI ports, push it fully in, close the latch. At the camera end
   the contacts face the camera board.
2. Boot, then: `rpicam-hello --list-cameras` lists `imx708_noir`.
   `No cameras available!` means the ribbon is loose or reversed at one of
   the two ends (on 2026-11-13, reseating both ends fixed it). The `WARN ...
   No static properties available for 'imx708_noir'` lines are harmless.
3. Test photo: `rpicam-still -n --autofocus-on-capture --width 1920 --height 1080 -o ~/test.jpg`;
   copy it to the laptop with `scp tonypi@<pi-address>:test.jpg .` (PowerShell).

NoIR means no infrared filter: colours look pinkish in daylight and under grow
lights. In exchange it can see in the dark with an infrared lamp.

**Check:** the collector log shows `Camera: photo photos/<date>/<time>.jpg (... KB)`
and the dashboard's **Camera** card shows it. A camera problem only logs a
warning: the sensors keep running.

## 8. Run it as a service (survives reboots and power cuts)

Edit `User=` and the paths in `deploy/attic-collector.service` if the user is
not `tonypi`, then:

```bash
sudo cp deploy/attic-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now attic-collector
systemctl status attic-collector
```

**Check:**

- `journalctl -u attic-collector -f` shows readings every 5 minutes;
- `sudo reboot` → after a few minutes the dashboard updates again by itself;
- pull the power for a second → same result (Mosquitto and HA have
  `restart: unless-stopped`, the collector has `Restart=always`).

## 9. Phone access

**At home (setup day):** phone on the same Wi-Fi as the Pi, open
`http://192.168.1.198:8123`, or install the **Home Assistant** app and enter
that address. Nothing else is needed.

**From anywhere (the Pi on 4G in the attic): Tailscale.**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # opens a login link: use the personal account
tailscale ip -4              # the Pi's Tailscale address (100.x.y.z)
```

On the phone: install **Tailscale**, log in with the same personal account,
switch it on. Then open `http://<pi-name>:8123` (the name shown in the
Tailscale app) or `http://100.x.y.z:8123`. In the Home Assistant app, set this
as the server address.

**Check:** with the phone on mobile data (Wi-Fi off), the dashboard loads.

**Tailscale on this Pi (2026-11-13):** tailnet on the personal GitHub login,
`tony-rpi` = `100.80.188.65`. In the admin page (tony-rpi → ⋯) choose
**Disable key expiry**, otherwise the Pi logs itself out after about 6 months
with nobody there to log it back in. Another person's phone joins through
**Share** on the same menu: they install Tailscale with their own account and
accept the invitation; they then see only this Pi.

## 9b. The 4G modem (ZTE MF833N)

It shows up as the network interface `eth1` (`192.168.0.x`, gateway and web
page at `192.168.0.1`). Its route wins over the Wi-Fi's, so **when the modem
loses 4G the whole Pi loses the internet, even with Wi-Fi on**. A sign of that:
every name resolves to `192.168.50.x` (the modem answers for everything).

**Status** (no login needed, the `Referer` header is):

```bash
curl -s -H "Referer: http://192.168.0.1/index.html" "http://192.168.0.1/goform/goform_get_cmd_process?isTest=false&multi_data=1&cmd=ppp_status,signalbar,network_type,network_provider"; echo
```

`ppp_connected` is online; `ppp_disconnected` is not. On 2026-11-13: no PIN,
DIGI PT, LTE, 5 bars, connection mode Automatic (`auto_dial`). Even so it
dropped once and did not reconnect by itself. This brought it back at once:

```bash
curl -s -H "Referer: http://192.168.0.1/index.html" -d "isTest=false&goformId=CONNECT_NETWORK" http://192.168.0.1/goform/goform_set_cmd_process; echo
```

The modem's own web page, from the laptop (PowerShell), through the Pi:
`ssh -L 8080:192.168.0.1:80 tonypi@<pi-address>`, then open
`http://localhost:8080`.

### MTU 1440 (needed for Tailscale over 4G)

The mobile network silently drops packets bigger than about 1460 bytes. Small
things (ping, curl) work; Tailscale's large first message never arrives, so
Tailscale stays offline on 4G only. Measure it (1460 passes, 1470 does not):

```bash
for size in 1472 1442 1432 1412; do ping -c 1 -W 3 -M do -s $size -I eth1 8.8.8.8 >/dev/null && echo "$((size+28)) ok" || echo "$((size+28)) dropped"; done
```

Fix, kept across reboots by NetworkManager:

```bash
CONN=$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: '$2=="eth1"{print $1}')
sudo nmcli connection modify "$CONN" ethernet.mtu 1440
sudo nmcli connection up "$CONN"
```

**Check:** `ip link show eth1 | grep -o 'mtu [0-9]*'` prints `mtu 1440`, also after a reboot.

### The watchdog (reconnects the modem by itself)

`tools/modem_watchdog.py`, run every 2 minutes by a systemd timer, through
`eth1` only:

1. internet works → nothing (no log);
2. no internet and the modem says `ppp_disconnected` → `CONNECT_NETWORK`,
   test again 20 s later;
3. still nothing for 3 runs in a row (about 6 minutes) → restart the modem
   (`REBOOT_DEVICE`), at most once every 30 minutes.

No modem plugged in → it does nothing. It needs only the system `python3`.

```bash
sudo cp deploy/modem-watchdog.service deploy/modem-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now modem-watchdog.timer
```

**Check** (at home, so SSH stays up over Wi-Fi): drop the connection on
purpose, run the watchdog once, read what it did:

```bash
curl -s -H "Referer: http://192.168.0.1/index.html" -d "isTest=false&goformId=DISCONNECT_NETWORK" http://192.168.0.1/goform/goform_set_cmd_process; echo
sudo systemctl start modem-watchdog.service
journalctl -u modem-watchdog -n 10 --no-pager
```

It should log `No internet ... modem says ppp_disconnected`, then
`Reconnected`. `systemctl list-timers modem-watchdog` shows the next run.

### Testing "4G only" at home without locking yourself out

Turning Wi-Fi off is remembered across reboots, and without Wi-Fi only
Tailscale over 4G reaches the Pi. Schedule the undo **first**:

```bash
sudo systemd-run --unit=wifi-back-on --on-active=10min /usr/bin/nmcli radio wifi on
sudo nmcli radio wifi off
```

Wi-Fi comes back by itself after 10 minutes (`systemctl list-timers` shows the
countdown). Do not shut down or reboot during the test: the undo exists only
while the Pi runs. Use another unit name (`wifi-back-on-2`) for a second test
the same day.

## 10. Day-to-day

| Task | Command (on the Pi, in `~/attic-hydroponics`) |
|---|---|
| Update the code | `git pull && sudo systemctl restart attic-collector` |
| Change the interval | edit `interval_seconds` in `config/collector.pi.yaml`, restart the service |
| Force a test value | `.venv/bin/python -m collector force air_temperature 30 --minutes 10 --config config/collector.pi.yaml` |
| Remove forced values | `.venv/bin/python -m collector clear --config config/collector.pi.yaml` |
| Collector logs | `journalctl -u attic-collector -f` or `logs/collector.log` |
| Broker / HA logs | `docker compose logs -f mosquitto` / `homeassistant` |
| 4G modem status | the `curl` in part 9b, or `python3 tools/modem_watchdog.py --status` |
| Watchdog log | `journalctl -u modem-watchdog -n 20` (only writes when something is wrong) |
| Photos | `ls photos/` (one folder per day, kept 7 days) |
| Watch raw MQTT | `docker compose exec mosquitto mosquitto_sub -u homeassistant -P '<MQTT_HA_PASSWORD>' -t 'attic/#' -v` |

Upgrading Mosquitto or Home Assistant is deliberate: change the pinned tag in
`docker-compose.yml`, then `docker compose pull && docker compose up -d`.

## Later (not needed on the Pi day)

- **Xiaomi Bluetooth sensors (QBH4312GL):** Home Assistant needs the Pi's
  Bluetooth, which in Docker means `network_mode: host` and access to D-Bus
  for the `homeassistant` service. Test one sensor before buying more; newer
  models need an encryption key from the Xiaomi account.
- **Real reservoir sensor:** change `reservoir_level` to the new sensor type in
  `config/collector.pi.yaml`. Dashboard and topics stay the same.
- **Alerts:** thresholds as `input_number` helpers, notifications that clear
  themselves; test with the `force` command and by stopping the collector.
- **Backups:** copy `homeassistant/config/` (with HA stopped) and `.env`
  somewhere off the Pi now and then.
