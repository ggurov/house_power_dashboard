#!/bin/sh
# Deploy pi-sampler to the Pi over SSH. Run from this directory.
# Usage: SAMPLER_HOST=pi@192.168.1.28 ./install.sh
set -e
HOST="${SAMPLER_HOST:?set SAMPLER_HOST, e.g. SAMPLER_HOST=pi@192.168.1.28}"
SSH="ssh -o PubkeyAuthentication=no"
SCP="scp -o PubkeyAuthentication=no"

echo "== copy sampler =="
$SCP sampler.py sampler.env.example "$HOST:/tmp/"
$SCP -r ads1256_vendor "$HOST:/tmp/house-power-vendor"

echo "== install on Pi =="
$SSH "$HOST" 'set -e
  sudo mkdir -p /opt/house-power /etc/house-power
  sudo cp /tmp/sampler.py /opt/house-power/sampler.py
  sudo rm -rf /opt/house-power/ads1256_vendor
  sudo cp -r /tmp/house-power-vendor /opt/house-power/ads1256_vendor
  if [ ! -f /etc/house-power/sampler.env ]; then
    sudo cp /tmp/sampler.env.example /etc/house-power/sampler.env
    echo "EDIT /etc/house-power/sampler.env (RANGE_AMPS must match clamp jumpers)"
  fi
  sudo python3 -c "import spidev, RPi.GPIO; print(\"spi/gpio ok\")"
  sudo python3 -c "import paho.mqtt.client; print(\"mqtt ok\")" || \
    sudo apt-get install -y python3-paho-mqtt'

echo "== enable systemd unit =="
$SCP pi-sampler.service "$HOST:/tmp/"
$SSH "$HOST" 'set -e
  sudo cp /tmp/pi-sampler.service /etc/systemd/system/pi-sampler.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now pi-sampler
  sleep 2
  sudo systemctl is-active pi-sampler
  echo "--- disable legacy rc.local sampler (kept as backup, not running) ---"
  sudo sed -i "s|^/root/ADS1256_graphite/run.sh|# /root/ADS1256_graphite/run.sh  # superseded by pi-sampler.service|" /etc/rc.local || true'

echo "done. Verify: backend dashboard should show live watts within seconds."
