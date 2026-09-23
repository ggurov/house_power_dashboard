# Installing house_power_dashboard

Two ways to install: hand this repo to an AI coding agent (recommended —
the Pi is slow and the deploy has sharp edges), or follow the manual guide
below.

## Option A — AI agent install (recommended)

Copy-paste the prompt in the [Quick start](README.md#quick-start) section
of the README into your agent, filling in the blanks.

## Option B — manual install

Prerequisites: Pi 3B+ (or better) with Raspberry Pi OS, the Waveshare
High-Precision AD/DA shield stacked on it, two CT clamps installed on the
mains feeders with known jumper settings, and SSH access from this machine.

```bash
git clone https://github.com/ggurov/house_power_dashboard.git
cd house_power_dashboard/pi-hosted
export SAMPLER_HOST=pi@<your-pi-ip>
./install.sh launch    # copy files, start slow apt/venv stages on the Pi
./install.sh status    # poll progress (20–40 min on a Pi 3, it is slow)
./install.sh finish    # install units, stop legacy sampler, start services
```

With password (not key) SSH, install your key first
(`ssh-copy-id pi@<pi-ip>`) — wrapping the script in `sshpass` stalls.

Then, on the Pi, confirm `/etc/house-power/sampler.env`:

```ini
RANGE_AMPS=100        # MUST match the physical clamp jumpers
MQTT_HOST=localhost
HTTP_URL=http://localhost:8000/api/v1/readings
# LEG1_CHANNELS=0,2,4,6
# LEG2_CHANNELS=1,3,5,7
# CALIBRATION=1.0     # only after a known-load measurement says otherwise
```

Verify:

```bash
sudo systemctl is-active pi-sampler house-power-backend mosquitto postgresql
curl http://localhost:8000/api/v1/health        # {"ok":true,"db":true,...}
curl http://localhost:8000/api/v1/current       # plausible per-leg amps
ps aux | grep '[m]ain.py'                        # must print nothing (no legacy reader)
sudo journalctl -u pi-sampler -f                 # zero_retries should stay ~flat
```

Dashboard: `http://<pi-ip>:8000/` (LAN only). Finish with a known-load
test (kettle on a 120 V circuit → one leg steps ~+10–12 A within a second).

## Surviving reboots

Everything is systemd-enabled and comes back on its own — no manual steps
after a power cut. Verified with `systemctl is-enabled`:

| Unit | Role | Order |
|---|---|---|
| `postgresql` | readings + rollup tables | first (others require it) |
| `mosquitto` | MQTT broker (localhost:1883) | early |
| `house-power-backend` | API + dashboard :8000 | after postgres + mosquitto |
| `pi-sampler` | ADC loop → MQTT | after mosquitto; self-heals if the broker isn't up yet (queued publish retries forever) |
| `house-power-rollup.timer` | 5-min aggregate refresh + 90-day retention | after postgres |

```bash
systemctl is-enabled pi-sampler house-power-backend house-power-rollup.timer mosquitto postgresql
```

Cold-boot tested 2026-09-23: SSH reachable ~2 min after `reboot`, all
services active, no legacy reader, backend healthy with fresh readings, and
the sampler's first 60 reads needed zero corrections.

The legacy graphite sampler cannot come back: its `/etc/rc.local` line is
commented out and its root cron entry was already disabled. If you ever need
to confirm no second SPI master exists: `ps aux | grep '[m]ain.py'` must
print nothing.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Legs identical / loads invisible | Two processes sharing SPI (legacy `./main.py` survived) | Kill by argv (see prompt step 6), verify single master |
| Readings swing 0 ↔ huge second-to-second | Same as above, or pre-median sampler | Current sampler medians each clamp's 4 fanned channels; check journal corrections |
| `ID Read failed` in sampler log | Benign on this shield; sampler continues (legacy behavior) | Ignore unless reads are all zero |
| Backend `db:false` in health | Table ownership after manual schema load | `ALTER TABLE readings, power_* OWNER TO house_power` (installer handles this) |
| History looks flat after a fix | Stale bad-data window | DELETE the window from `readings` + `power_*` tables |
| Absolute amps ~20% off but split correct | Clamp seating (air gap) or nameplate optimism | Reseat/clean clamps, verify with plug meter, then set `CALIBRATION` |
| `pip install` spins forever on Pi | Unpinned deps backtracking | Use the pinned versions in `stage3-venv.sh` |

## Upgrading

```bash
git pull
# re-copy what changed (sampler, backend, dashboard, units) and restart:
# same file layout as install.sh finish; or re-run finish after launch files update
sudo systemctl restart pi-sampler house-power-backend
```
