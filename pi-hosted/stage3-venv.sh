#!/bin/bash
# Stage 3 (runs ON the Pi as root): backend Python venv.
# No Node, no Docker — plain pip (piwheels provides armhf wheels).
set -e
python3 -m venv /opt/house-power/venv 2>/dev/null || true
/opt/house-power/venv/bin/pip install fastapi uvicorn "psycopg[binary]" paho-mqtt
echo "VENV-EXIT:$?"
/opt/house-power/venv/bin/python -c "import fastapi, uvicorn, psycopg, paho.mqtt.client; print('backend-deps-ok')"
touch /tmp/pi-hosted-step3.done
