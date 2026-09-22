#!/bin/bash
# Stage 2 (runs ON the Pi as root): system packages, postgres, mosquitto, env files.
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y mosquitto postgresql-13 python3-venv libpq5
cp /tmp/mosquitto-pi.conf /etc/mosquitto/conf.d/house-power.conf
systemctl enable --now mosquitto postgresql
# low-memory tuning for the 1 GB Pi
printf 'shared_buffers = 64MB\neffective_cache_size = 512MB\nmax_connections = 20\n' \
  > /etc/postgresql/13/main/conf.d/house-power.conf
systemctl restart postgresql

mkdir -p /etc/house-power
if [ ! -f /etc/house-power/backend.env ]; then
  DBPW=$(python3 -c "import secrets; print(secrets.token_hex(16))")
  sed "s/CHANGE_ME/$DBPW/" /tmp/backend.env.example > /etc/house-power/backend.env
  chmod 600 /etc/house-power/backend.env
fi
if [ ! -f /etc/house-power/sampler.env ]; then
  sed -e "s/^MQTT_HOST=.*/MQTT_HOST=localhost/" \
      -e "s|^#*HTTP_URL=.*|HTTP_URL=http://localhost:8000/api/v1/readings|" \
      /tmp/sampler.env.example > /etc/house-power/sampler.env
  echo "EDIT /etc/house-power/sampler.env: RANGE_AMPS must match clamp jumpers"
fi
chmod 600 /etc/house-power/sampler.env

# shellcheck disable=SC1091
. /etc/house-power/backend.env
DBPW=$(echo "$DB_DSN" | sed "s|.*://house_power:||; s|@.*||")
export PGPASSWORD="$DBPW"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'house_power') THEN
    CREATE ROLE house_power LOGIN PASSWORD '$DBPW';
  ELSE
    ALTER ROLE house_power WITH PASSWORD '$DBPW';
  END IF;
END \$\$;
SQL
sudo -u postgres createdb -O house_power house_power 2>/dev/null || true
sudo -u postgres psql -d house_power -v ON_ERROR_STOP=1 -f /tmp/schema-pg.sql
# schema may have been created by the postgres superuser on first run —
# the backend role must own the tables (it runs CREATE INDEX IF NOT EXISTS).
sudo -u postgres psql -d house_power -v ON_ERROR_STOP=1 -c \
  "ALTER TABLE IF EXISTS readings OWNER TO house_power;
   ALTER TABLE IF EXISTS power_1min OWNER TO house_power;
   ALTER TABLE IF EXISTS power_1hour OWNER TO house_power;
   ALTER TABLE IF EXISTS power_day OWNER TO house_power;"
touch /tmp/pi-hosted-step2.done
