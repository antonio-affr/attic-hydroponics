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

**Check:** both services `Up`; `docker compose logs mosquitto` ends with
`mosquitto version ... running`.

**Why 0.0.0.0 is safe here:** the Pi's only network is the 4G modem, whose
mobile network refuses incoming connections from the internet. 0.0.0.0 lets
Home Assistant answer on the home Wi-Fi during setup and on Tailscale later.
Never forward port 8123 or 1883 on any router.

## 5. Home Assistant first-time setup

Same clicks as on Windows, from a laptop or phone on the same network as the Pi:

1. Open `http://<pi-address>:8123` (at home: `http://192.168.1.70:8123`).
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

## 7. The real Grove sensor

1. Find the sensor on the I2C bus: `i2cdetect -y 1` (note the address, e.g. `0x44`).
2. Run the existing script once on its own and confirm it prints sensible values.
3. Fill in `collector/sensors/grove.py` (the TODO list at the top of the file
   explains exactly what to do), and put the sensor settings in the `options:`
   block of `config/collector.pi.yaml`.
4. Add the sensor's Python library to `requirements.txt` and reinstall
   (`.venv/bin/pip install -r requirements.txt`). If it needs the hardware
   group, the Pi user must be in `i2c` (`groups` shows it).
5. Try it with a short interval:

```bash
.venv/bin/python -m collector run --config config/collector.pi.yaml --interval 30
```

**Check:** the log shows `Published air_temperature=..., humidity=...` with
believable values; breathe on the sensor and humidity rises. A missing or
broken sensor shows `reading rejected, not published` and HA marks it stale
after 2.5 × the interval. That is the intended behaviour.

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
`http://192.168.1.70:8123`, or install the **Home Assistant** app and enter
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

## 10. Day-to-day

| Task | Command (on the Pi, in `~/attic-hydroponics`) |
|---|---|
| Update the code | `git pull && sudo systemctl restart attic-collector` |
| Change the interval | edit `interval_seconds` in `config/collector.pi.yaml`, restart the service |
| Force a test value | `.venv/bin/python -m collector force air_temperature 30 --minutes 10 --config config/collector.pi.yaml` |
| Remove forced values | `.venv/bin/python -m collector clear --config config/collector.pi.yaml` |
| Collector logs | `journalctl -u attic-collector -f` or `logs/collector.log` |
| Broker / HA logs | `docker compose logs -f mosquitto` / `homeassistant` |
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
