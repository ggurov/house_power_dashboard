# Pi-hosted (all-in-one) deployment

Everything — sampler, MQTT broker, PostgreSQL, backend API, dashboard —
runs on the Pi itself: it is the only always-on host on the Pi's LAN.

Trade-offs of the Pi-hosted choice (vs a TimescaleDB host, should the
project ever move off-Pi):

- **No TimescaleDB**: no 32-bit armhf build exists, so this uses stock
  PostgreSQL 13 with plain rollup tables (`schema-pg.sql`, same names and
  columns as the continuous aggregates) refreshed every 5 min by
  `house-power-rollup.timer` (`refresh.sql` also enforces the 90-day raw
  retention). The backend code is identical; `db.ensure_schema()` picks the
  plain-table path automatically.
- **No Docker / Node on the Pi**: mosquitto + postgres come from apt, the
  backend runs in a Python venv (piwheels for armhf wheels). The dashboard
  is static files served by the backend — nothing to build.
- **Migration path**: `pg_dump house_power` restores straight into a
  TimescaleDB instance later; add continuous aggregates over `readings`
  following the standard TimescaleDB docs. No sampler changes needed.

## Install

```bash
SAMPLER_HOST=pi@192.168.1.28 ./install.sh        # full: copy + stages + finish
# or step by step:
SAMPLER_HOST=pi@192.168.1.28 ./install.sh launch  # copy files, start slow stages
SAMPLER_HOST=pi@192.168.1.28 ./install.sh status  # poll apt/venv progress
SAMPLER_HOST=pi@192.168.1.28 ./install.sh finish  # units on, legacy off, start
```

With password (not key) SSH, `sshpass` cannot reliably wrap the whole
script — run the equivalent commands one by one, or install your key first:
`ssh-copy-id pi@192.168.1.28`.

## Layout on the Pi

- `/opt/house-power/`: `sampler.py`, `ads1256_vendor/`, `venv/`, `backend/*.py`,
  `dashboard/`, `nilm/`, `refresh.sql`
- `/etc/house-power/`: `sampler.env`, `backend.env` (generated `DB_DSN`
  password, mode 600). Never in git.
- Services: `pi-sampler`, `house-power-backend`, `house-power-rollup.timer`
  (+ stock `mosquitto`, `postgresql`) — all `systemctl enable`d, so a reboot
  or power cut recovers unattended (backend waits for postgres+mosquitto,
  sampler retries the broker forever). Legacy `/root/ADS1256_graphite/run.sh`
  is disabled in `/etc/rc.local` and its processes stopped.
- Dashboard: `http://<pi-ip>:8000/` (LAN only).
