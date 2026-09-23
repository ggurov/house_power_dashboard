# Installing house_power_dashboard

Two ways to install: hand this repo to an AI coding agent (recommended —
the Pi is slow and the deploy has sharp edges), or follow the manual guide
below.

## Option A — AI agent install (recommended)

Copy-paste the prompt below into your agent, filling in the blanks. It was
written from a real install and encodes every gotcha encountered (slow Pi,
SPI-bus contention, SSH password quirks, calibration).

```text
Install house_power_dashboard (https://github.com/ggurov/house_power_dashboard.git)
on my Raspberry Pi and verify it end to end.

Target Pi: <HOST, e.g. pi@192.168.1.28> / user <USER, usually pi>
SSH access: <key at path, or "password: ...">
Hardware: Waveshare High-Precision AD/DA shield (ADS1256) stacked on the Pi.
  2x FCS2151-SP-5V split-core clamps on the mains, jumper = <100|150|200> A
  (blank jumper = 100 A). Clamp A channels = <e.g. 0,2,4,6>,
  clamp B channels = <e.g. 1,3,5,7>.
Mains: <e.g. North America split-phase, ~120 V per leg>

Procedure:
1. Clone the repo, read README.md, pi-hosted/README.md, and AGENTS.md if present.
2. Probe first, install second: SSH to the Pi and record OS version,
   RAM/disk, `ls /dev/spi*`, existing sampler processes (`ps aux | grep -i ads`),
   and Python/apt package state. Never apt/pip install blindly on the Pi.
3. Deploy everything on the Pi with pi-hosted/install.sh
   (subcommands: launch, status, finish). No Docker or Node on the Pi:
   mosquitto + PostgreSQL come from apt, the backend runs in a venv
   (piwheels has the armhf wheels). Pin versions exactly as in
   pi-hosted/stage3-venv.sh — unpinned paho-mqtt sends pip into
   version-backtracking hell on a Pi 3.
4. If SSH uses a password (not a key), do NOT wrap the whole installer in
   sshpass — it stalls partway. Run each scp/ssh step with its own
   credentials instead (see pi-hosted/README.md).
5. Set RANGE_AMPS in /etc/house-power/sampler.env to match the physical
   clamp jumpers. Leave CALIBRATION=1.0 until a known-load test says otherwise.
6. Kill any legacy sampler before starting the new one: its command line is
   just `/usr/bin/python ./main.py` (working directory is invisible to
   `pkill -f`), so match the argv (`pkill -f 'python \./main\.py'`),
   escalate to -9 if needed, and VERIFY with ps. Two processes sharing the
   SPI bus corrupt every reading — this is the #1 failure mode.
7. Verify, in order: all four services active
   (pi-sampler, house-power-backend, mosquitto, postgresql); backend
   `/api/v1/health` shows db:true; `/api/v1/current` shows plausible,
   INDEPENDENT per-leg amps; exactly one Python sampler in ps; the
   sampler journal shows ~zero new zero_retries/zero_kept corrections.
8. Ask me to switch a known load (e.g. a kettle on a 120 V circuit) on/off
   and confirm exactly one leg steps by the expected amps within 1–2 s.
   If any fix changed readings mid-deploy, purge the bad window from
   PostgreSQL (`DELETE FROM readings WHERE ts < <cutoff>` + same for the
   power_* rollup tables).
9. Report the dashboard URL (http://<pi-ip>:8000, LAN only) and what was verified.

Hard constraints:
- NEVER commit secrets (*.env, passwords, tokens) to git. Keep them in
  /etc/house-power/ on the Pi (mode 600) and local .env files only.
- Do NOT dist-upgrade the Pi OS release, do NOT reboot without asking,
  do NOT run builds/agents/Node/Docker on the Pi.
- Commit repo changes often with clear messages; never push or open PRs
  unless I explicitly ask.
```

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
