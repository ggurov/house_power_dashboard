#!/bin/sh
# All-on-Pi deploy: sampler + mosquitto + PostgreSQL + backend + dashboard.
# No Docker, no Node on the Pi. Run from this directory:
#   SAMPLER_HOST=pi@192.168.1.28 ./install.sh
# Re-runnable: preserves /etc/house-power/*.env once created.
# Subcommands: launch (copy + start slow stages), status, finish, full (default).
set -e
HOST="${SAMPLER_HOST:?set SAMPLER_HOST, e.g. SAMPLER_HOST=pi@192.168.1.28}"
SSH="ssh -o PubkeyAuthentication=no"
SCP="scp -o PubkeyAuthentication=no"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cmd="${1:-full}"

do_launch() {
  echo "== copy files =="
  $SCP "$ROOT/pi-sampler/sampler.py" "$ROOT/pi-sampler/sampler.env.example" "$HOST:/tmp/"
  $SCP -r "$ROOT/pi-sampler/ads1256_vendor" "$HOST:/tmp/house-power-vendor"
  $SCP "$ROOT/backend/app.py" "$ROOT/backend/db.py" "$ROOT/backend/ingest.py" \
      "$ROOT/backend/mqtt_ingest.py" "$HOST:/tmp/house-power-backend-py"
  $SCP -r "$ROOT/dashboard" "$ROOT/nilm" "$HOST:/tmp/house-power-static"
  $SCP "$ROOT/pi-hosted/stage2-db.sh" "$ROOT/pi-hosted/stage3-venv.sh" \
      "$ROOT/pi-hosted/schema-pg.sql" "$ROOT/pi-hosted/refresh.sql" \
      "$ROOT/pi-hosted/mosquitto-pi.conf" "$ROOT/pi-hosted/backend.env.example" \
      "$ROOT/pi-sampler/pi-sampler.service" \
      "$ROOT/pi-hosted/house-power-backend.service" \
      "$ROOT/pi-hosted/house-power-rollup.service" \
      "$ROOT/pi-hosted/house-power-rollup.timer" "$HOST:/tmp/"
  echo "== launch slow stages in background on the Pi =="
  $SSH "$HOST" 'rm -f /tmp/pi-hosted-step2.done /tmp/pi-hosted-step3.done
    nohup sudo bash /tmp/stage2-db.sh > /tmp/pi-hosted-step2.log 2>&1 &
    nohup sudo bash /tmp/stage3-venv.sh > /tmp/pi-venv.log 2>&1 &
    echo launched'
}

do_status() {
  $SSH "$HOST" 'echo "--- step2 (system/db):"; ls /tmp/pi-hosted-step2.done 2>&1
    tail -2 /tmp/pi-hosted-step2.log 2>&1
    echo "--- step3 (venv):"; ls /tmp/pi-hosted-step3.done 2>&1
    tail -2 /tmp/pi-venv.log 2>&1; uptime' 2>&1
}

do_finish() {
  echo "== deploy code + units, stop legacy sampler, start services =="
  $SSH "$HOST" 'set -e
    sudo mkdir -p /opt/house-power/backend /etc/house-power
    sudo cp /tmp/sampler.py /opt/house-power/sampler.py
    sudo rm -rf /opt/house-power/ads1256_vendor
    sudo cp -r /tmp/house-power-vendor /opt/house-power/ads1256_vendor
    sudo cp /tmp/house-power-backend-py/*.py /opt/house-power/backend/
    sudo rm -rf /opt/house-power/dashboard /opt/house-power/nilm
    sudo cp -r /tmp/house-power-static/dashboard /tmp/house-power-static/nilm /opt/house-power/
    sudo cp /tmp/refresh.sql /opt/house-power/refresh.sql
    sudo cp /tmp/pi-sampler.service /tmp/house-power-backend.service \
            /tmp/house-power-rollup.service /tmp/house-power-rollup.timer \
            /etc/systemd/system/
    sudo systemctl daemon-reload
    # stop legacy graphite sampler (rc.local loop + current process).
    # NOTE: its cmdline is just "/usr/bin/python ./main.py" (CWD is invisible
    # to pkill -f), so match the argv, escalate, and verify — a stale reader
    # sharing the SPI bus corrupts every reading.
    sudo sed -i "s|^/root/ADS1256_graphite/run.sh|# /root/ADS1256_graphite/run.sh  # superseded by pi-sampler.service|" /etc/rc.local || true
    sudo pkill -f ADS1256_graphite/run.sh || true
    sleep 2
    sudo pkill -f 'python \./main\.py' || true
    sleep 5
    if sudo ps -eo args | grep -q 'python \./main[.]py'; then
      echo "legacy still alive after TERM, escalating to KILL"
      sudo pkill -9 -f 'python \./main\.py' || true
      sleep 5
    fi
    if sudo ps -eo args | grep -q 'python \./main[.]py'; then
      echo "ERROR: legacy sampler still alive, aborting"; exit 1
    fi
    echo "legacy stopped; single SPI master confirmed"
    sudo systemctl enable --now pi-sampler house-power-backend house-power-rollup.timer
    sleep 3
    sudo systemctl is-active pi-sampler house-power-backend
    echo "--- backend health ---"
    curl -sf http://localhost:8000/api/v1/health; echo'
  echo "done. Dashboard: http://192.168.1.28:8000/ (from your LAN)"
}

case "$cmd" in
  launch) do_launch ;;
  status) do_status ;;
  finish) do_finish ;;
  full)
    do_launch
    echo "== waiting for slow stages (20-40 min on a Pi 3) =="
    for _ in $(seq 1 100); do
      if $SSH "$HOST" "test -f /tmp/pi-hosted-step2.done -a -f /tmp/pi-hosted-step3.done" 2>/dev/null; then
        echo "stages done"; break
      fi
      sleep 60
    done
    do_finish ;;
  *) echo "usage: $0 [launch|status|finish|full]"; exit 1 ;;
esac
