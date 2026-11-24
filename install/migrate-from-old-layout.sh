#!/bin/sh
# Move an older install to the layout with a .env file. Run it from the unpacked repository, before install.sh:
#   sh install/migrate-from-old-layout.sh
# In the older install the meter address and the Signal values sit inside collector.py and notify_signal.py.
# The script does these steps and stops at the first error:
#   1. Archive the old program files, without the database, in the backup folder (mode 600).
#   2. Read the meter address and the Signal values from the old files and write them to .env (mode 600).
#   3. List the Environment= variables of the systemd units. install.sh replaces the units.
# It never prints a Signal number or address. It never replaces an existing .env. It changes no program file.
set -eu
umask 077

APP_DIR="${HW_BASE_DIR:-/opt/homewizard}"
BACKUP_DIR="${HW_BACKUP_DIR:-/var/backups/homewizard}"
UNIT_DIR="${HW_UNIT_DIR:-/etc/systemd/system}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$APP_DIR/collector.py" ]; then
    echo "No install found in $APP_DIR. Nothing to move."
    exit 0
fi
if [ -f "$APP_DIR/config.py" ]; then
    echo "$APP_DIR already uses the layout with .env. Nothing to move."
    exit 0
fi

mkdir -p "$BACKUP_DIR"
archive="$BACKUP_DIR/opt_homewizard_before_move_$(date +%Y%m%d_%H%M%S).tar.gz"
tar --exclude='homewizard.db*' --exclude='__pycache__' -czf "$archive" \
    -C "$(dirname "$APP_DIR")" "$(basename "$APP_DIR")"
echo "Archive of the old program files: $archive"

python3 -I - "$APP_DIR" "$REPO_DIR/.env.example" <<'PY'
import os
import re
import sys

app_dir, template_path = sys.argv[1], sys.argv[2]
env_path = os.path.join(app_dir, ".env")
if os.path.exists(env_path):
    print(f"{env_path} exists. It is not changed.")
    raise SystemExit(0)


def read(name):
    try:
        with open(os.path.join(app_dir, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


wanted = ("HW_METER_URL", "HW_SIGNAL_URL", "HW_SIGNAL_FROM", "HW_SIGNAL_TO")
found = {}
match = re.search(r"""^URL\s*=\s*["']([^"']+)["']""", read("collector.py"), re.M)
if match:
    found["HW_METER_URL"] = match.group(1)
notify = read("notify_signal.py")
for key in wanted[1:]:
    match = re.search(r"""getenv\(\s*["']%s["']\s*,\s*["']([^"']*)["']""" % key, notify)
    if match and match.group(1):
        found[key] = match.group(1)

with open(template_path, encoding="utf-8") as f:
    text = f.read()
for key, value in found.items():
    text, count = re.subn(r"^%s=.*$" % key, lambda _m, k=key, v=value: f"{k}={v}", text, flags=re.M)
    if count != 1:
        raise SystemExit(f"{key} is not in the template {template_path}")

fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text)
print(f"Wrote {env_path} (mode 600).")
print("Taken from the old files: " + (", ".join(sorted(found)) or "nothing"))
missing = [key for key in wanted if key not in found]
if missing:
    print("Not found, set by hand: " + ", ".join(missing))
PY

vars=""
for unit in "$UNIT_DIR"/homewizard-*.service; do
    [ -f "$unit" ] || continue
    names="$(sed -n -E 's/^Environment=("?)([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$unit")"
    vars="$vars $names"
done
vars="$(echo $vars | tr ' ' '\n' | sort -u | tr '\n' ' ' | sed 's/ $//')"
if [ -n "$vars" ]; then
    echo "These variables are set in a systemd unit: $vars"
    echo "install.sh replaces the unit files. Copy them to $APP_DIR/.env first."
fi

echo
echo "Next steps:"
echo "  1. Check the file:        nano $APP_DIR/.env"
echo "  2. Install the new files: sh $REPO_DIR/install/install.sh"
echo "  3. Check the log:         journalctl -u homewizard-collector.service -n 30 --no-pager"
echo "To go back: tar -xzf $archive -C $(dirname "$APP_DIR") && systemctl restart homewizard-collector.service homewizard-api.service"
