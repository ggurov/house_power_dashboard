# house_power_dashboard

Realtime + historical dashboard for a North-American split-phase home (2 × 120 V legs),
measured with two Loulensy 0–5 V DC split-core CTs (one per mains leg) read by a
Waveshare ADS1256 24-bit ADC on a Raspberry Pi 3 (`adcpi1`).

## How it works

```
CT leg1 ─┐
          ├─► ADS1256 (SPI) ─► pi-sampler (Pi, dumb loop) ──MQTT──┐
CT leg2 ─┘                                          └─HTTP fallback─┐
                                                                    ▼
                                              backend (off-Pi, FastAPI)
                                                ├─► TimescaleDB (raw 90d, aggregates forever)
                                                ├─► SSE live stream ─► dashboard (static page)
                                                └─► NILM module (optional, feature-flagged)
```

The Pi only samples and publishes. Everything heavy (TSDB, API, dashboard, NILM)
runs off-Pi in Docker.

## Layout

| Path | Runs on | Description |
|---|---|---|
| `pi-sampler/` | Pi | Minimal Python loop: ADS1256 → amps → MQTT (+HTTP fallback). No builds, no Docker. |
| `backend/` | workstation/server (Docker) | FastAPI ingest + history + SSE, MQTT subscriber, serves `dashboard/`. |
| `dashboard/` | served by backend | Dependency-free static page: live W per leg + total, history graphs. |
| `nilm/` | backend sidecar (flagged) | Exploratory power-signature event detector. Off by default. |
| `db/init.sql` | TimescaleDB | Hypertable, continuous aggregates, retention policies. |

## Sampler schema (versioned, v1)

```json
{"v": 1, "ts": 1727123456.123, "leg1_A": 12.34, "leg2_A": 11.02, "range_setting": 100, "host": "adcpi1"}
```

- `amps = volts / 5 * range_setting` (`range_setting` ∈ 100/150/200, must match the clamp jumper).
- Power is computed downstream: `W = 120 × I` per leg, `total = V1·I1 + V2·I2`. Never 240 V single-leg.

## Quick start (off-Pi)

```bash
cp .env.example .env   # set POSTGRES_PASSWORD
docker compose up --build
# dashboard → http://localhost:8000/   api → http://localhost:8000/api/v1/current
```

## Pi deploy

```bash
cd pi-sampler
SAMPLER_HOST=192.168.1.28 ./install.sh   # copies files, installs systemd unit, restarts
```

Range jumper **must** match `RANGE_AMPS` in `/etc/house-power/sampler.env`
(default 100). Verify against a known load (e.g. a ~1500 W kettle ≈ 12.5 A on one leg)
before trusting calibration.

## Data retention

- Raw 1 s readings: 90 days, then dropped.
- `power_1min` / `power_1hour` / `power_day` continuous aggregates: kept indefinitely.
- Precision degrades with age by design; see `db/init.sql`.

## NILM (exploratory)

Device discovery by power signature lives in `nilm/` and is **disabled by default**
(`ENABLE_NILM=false`). It never blocks the sampler or dashboard path.

## Security notes

- No secrets in git. Pi password / DB passwords live in env files only (`sampler.env`, `.env`).
- MQTT allows anonymous clients on the LAN (MVP); put the broker behind your firewall,
  never expose it to the internet. See `mosquitto/mosquitto.conf`.
