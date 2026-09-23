# house_power_dashboard

Realtime + historical power dashboard for a North-American split-phase home
(2 × 120 V legs). Two split-core CT clamps on the mains feeders are read once
per second by a 24-bit ADC on a Raspberry Pi, stored with 90 days of full
resolution plus indefinite downsampled aggregates, and served on a
dependency-free web page with live and history charts.

<img src="pics/IMG_9041.png" width="300" alt="Live dashboard on a phone">

## How it works

```
CT leg1 (4x fanned) ─┐
                      ├─► ADS1256 (SPI) ─► pi-sampler ──MQTT──┐
CT leg2 (4x fanned) ─┘                          └─HTTP fallback─┐
                                                                ▼
                                         backend (FastAPI, same Pi)
                                           ├─► PostgreSQL (raw 90 d, rollups forever)
                                           ├─► SSE live stream ─► dashboard (static page)
                                           └─► NILM module (optional, feature-flagged)
```

The Pi does sampling, storage, API, and dashboard all-in-one (no Docker or
Node on the Pi — mosquitto + PostgreSQL come from apt, the backend runs in a
venv, the dashboard is static files). The backend's DB layer also supports
TimescaleDB (continuous aggregates) if the project ever moves off-Pi; on the
Pi it uses plain-PostgreSQL rollup tables with identical names and shapes.

## Hardware

**Pi + ADC shield.** Raspberry Pi 3 with a Waveshare High-Precision AD/DA
shield (ADS1256 24-bit ADC over SPI) stacked on top. Each CT output is fanned
out to 4 ADC channels; the sampler takes the median per group so a single bad
conversion can never move a reading. VCC/VREF jumpers are set to 5 V.

<img src="pics/IMG_9037.jpg" width="700" alt="Waveshare AD/DA shield on the Pi">

**CT clamps.** Two Loulensy/Furison `FCS2151-SP-5V` split-core transducers, one
per mains leg. Native `0–5 V DC analog` output (self-powered, 1% FS,
average-responding), two screw terminals, on-device calibration pots. **Not**
SCT-013s — no burden-resistor or DC-bias circuitry. The range jumper is blank,
which the label confirms is the `0–100 A` range:

<img src="pics/IMG_9038.jpg" width="500" alt="FCS2151-SP-5V label: output 5Vdc, jumper None = 0-100A">

**Panel install.** Both clamps around the two main feeders, Pi mounted beside
the panel:

<img src="pics/IMG_9040.jpg" width="700" alt="Breaker panel with both CT clamps and the Pi">

## Dashboard

Dependency-free static page (no framework, no build step): big live total,
per-leg watts/amps, a 10-minute live chart preloaded from storage on every
page load, and a history chart with 1H → ALL ranges. History queries send the
chart's pixel width and the server returns one aggregate bucket per pixel, so
a year view ships ~1k points instead of ~20k.

<img src="pics/IMG_9042.png" width="300" alt="History chart with range buttons">

## Data

**Sampler schema (versioned, v1)** — extensible without breaking ingest:

```json
{"v": 1, "ts": 1727123456.123, "leg1_A": 12.34, "leg2_A": 11.02,
 "range_setting": 100, "host": "adcpi1"}
```

- `amps = volts / 5 × range_setting`; power is computed downstream as
  `W = 120 × I` per leg, `total = V1·I1 + V2·I2` (never single-leg 240 V).
- Voltage is assumed (clamps measure current only); a `CALIBRATION`
  multiplier in the sampler env trims absolute scale once measured against a
  known load.

**Retention:** raw 1 s readings 90 days, then dropped; minute/hour/day
aggregates kept indefinitely (timer-refreshed rollup tables on the Pi;
continuous aggregates if ever moved to TimescaleDB).

**Validated end to end:** a 900 W coffee maker (@124 V mains = 7.3 A) reads
7.1 A on exactly one leg with 1-second step edges; relatch dropouts and
appliance steps are all visible in history.

## Layout

| Path | Description |
|---|---|
| `pi-sampler/` | Minimal Pi loop: ADS1256 → amps → MQTT (+HTTP fallback). Vendored Waveshare driver, systemd unit, mocked unit tests. |
| `pi-hosted/` | All-on-Pi installer: mosquitto + PostgreSQL + backend + rollup timer. No Docker/Node. |
| `backend/` | FastAPI ingest, history (pixel-bucketed), SSE stream, serves `dashboard/`. pytest suite. |
| `dashboard/` | Static HTML/CSS/vanilla-JS page, canvas charts. |
| `nilm/` | Exploratory power-signature event detector. `ENABLE_NILM=false` by default, never in the critical path. |
| `pics/` | Hardware and dashboard photos used above. |

## Quick start

Easiest path: paste the prompt below into an AI coding agent, filling in the
blanks — it encodes every gotcha from a real install (slow Pi, SPI-bus
contention, SSH password quirks, calibration). Manual steps, troubleshooting,
and boot behavior live in [`INSTALL.md`](INSTALL.md).

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

Or deploy manually — see [`INSTALL.md`](INSTALL.md).

All-on-Pi deploy (sampler, broker, DB, backend, dashboard on `adcpi1` —
see `pi-hosted/`):

```bash
cd pi-hosted
SAMPLER_HOST=pi@192.168.1.28 ./install.sh launch   # copy files, start slow stages
SAMPLER_HOST=pi@192.168.1.28 ./install.sh status   # poll apt/venv progress
SAMPLER_HOST=pi@192.168.1.28 ./install.sh finish   # units on, legacy off, start
# dashboard → http://192.168.1.28:8000/ (from your LAN)
```

`RANGE_AMPS` in the sampler env **must** match the physical clamp jumpers.
Verify against a known load before trusting calibration.

## Security notes

- No secrets in git. Pi/DB passwords live in env files only
  (`sampler.env`, `backend.env`, `.env`) — all gitignored.
- MQTT allows anonymous clients on the LAN (MVP); never expose port 1883 to
  the internet.
