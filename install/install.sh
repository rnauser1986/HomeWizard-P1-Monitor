#!/bin/sh
# Install or update HomeWizard P1 on a Debian host. Run as root from the unpacked repository:
#   sh install/install.sh
# The script can run again. It never replaces an existing .env.
# It restarts the two services, so the new files run at once. The alarms start counting from zero again.
set -eu

APP_DIR="${HW_BASE_DIR:-/opt/homewizard}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# An older install keeps its settings inside the .py files. New files over it would lose them.
if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/collector.py" ] && [ ! -f "$APP_DIR/config.py" ]; then
    echo "An older install is in $APP_DIR, and it has no .env file." >&2
    echo "Run this first: sh $REPO_DIR/install/migrate-from-old-layout.sh" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root." >&2
    exit 1
fi

apt-get update
apt-get install -y python3 sqlite3 tar gzip unzip curl ca-certificates
mkdir -p "$APP_DIR" /var/backups/homewizard

# Copy the application files. Do not copy the tests, the install folder or the .git folder.
for file in config.py collector.py api_server.py pq_data.py pq_page.py pq_route.py \
            tariff_mod.py notify_signal.py alarm_imbalance.py alarm_phase_amp.py \
            alarm_spike.py alarm_stale.py alarm_voltage.py ui.html .env.example; do
    cp "$REPO_DIR/$file" "$APP_DIR/$file"
done

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
fi

install -m 755 "$REPO_DIR/install/backup-homewizard-db.sh" /usr/local/bin/backup-homewizard-db.sh
for unit in homewizard-collector.service homewizard-api.service \
            homewizard-db-backup.service homewizard-db-backup.timer; do
    install -m 644 "$REPO_DIR/install/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now homewizard-db-backup.timer

# Start the two services only after the person set the address of the meter.
if grep -q '^HW_METER_URL=http://192.168.1.50/' "$APP_DIR/.env"; then
    echo "Edit $APP_DIR/.env and set HW_METER_URL to the address of your meter."
    echo "Then run: systemctl enable homewizard-collector.service homewizard-api.service"
    echo "And run:  systemctl restart homewizard-collector.service homewizard-api.service"
else
    systemctl enable homewizard-collector.service homewizard-api.service
    systemctl restart homewizard-collector.service homewizard-api.service
    systemctl is-active homewizard-collector.service homewizard-api.service
fi
