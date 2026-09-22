#!/bin/bash
# Stage 3 (runs ON the Pi as root): backend Python venv.
# No Node, no Docker — plain pip (piwheels provides armhf wheels).
set -e
python3 -m venv /opt/house-power/venv 2>/dev/null || true
# Pinned to the combo tested on the workstation (backend/requirements.txt).
# paho-mqtt MUST stay pinned: unpinned it sends pip into version backtracking.
/opt/house-power/venv/bin/pip install "fastapi==0.115.6" "uvicorn==0.34.0" \
  "psycopg[binary]==3.2.5" "paho-mqtt==1.6.1" || \
/opt/house-power/venv/bin/pip install "fastapi==0.115.6" "uvicorn==0.34.0" \
  "psycopg==3.2.5" "paho-mqtt==1.6.1"  # no binary wheel for armhf: use system libpq
echo "VENV-EXIT:$?"
/opt/house-power/venv/bin/python -c "import fastapi, uvicorn, psycopg, paho.mqtt.client; print('backend-deps-ok')"
echo "VENV-EXIT:$?"
/opt/house-power/venv/bin/python -c "import fastapi, uvicorn, psycopg, paho.mqtt.client; print('backend-deps-ok')"
touch /tmp/pi-hosted-step3.done
