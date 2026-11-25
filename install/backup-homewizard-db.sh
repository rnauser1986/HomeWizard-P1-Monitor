#!/bin/sh
# Daily backup of the HomeWizard P1 database and source files.
# 1. Copy the database with the SQLite backup command. This is safe while the collector writes.
# 2. Check the copy. Stop with an error if the check fails. Keep old backups in that case.
# 3. Compress the copy with gzip (homewizard_<stamp>.db.gz). The database is mostly repeated JSON, so it gets about 8 times smaller.
# 4. Archive the source files and .env. Do not include the database. Only the owner can read the files.
# 5. Delete backups older than RETENTION_DAYS.
set -eu
umask 077

APP_DIR="${HW_BASE_DIR:-/opt/homewizard}"
SRC="${HW_DB_PATH:-$APP_DIR/homewizard.db}"
DEST_DIR="${HW_BACKUP_DIR:-/var/backups/homewizard}"
RETENTION_DAYS="${HW_BACKUP_RETENTION_DAYS:-7}"

stamp="$(date +%Y%m%d_%H%M%S)"
db_copy="$DEST_DIR/homewizard_$stamp.db"
archive="$DEST_DIR/opt_homewizard_$stamp.tar.gz"

mkdir -p "$DEST_DIR"

sqlite3 "$SRC" ".backup '$db_copy'"

result="$(sqlite3 "$db_copy" 'PRAGMA integrity_check;')"
if [ "$result" != "ok" ]; then
    echo "[BACKUP_ERR] Integrity check failed for $db_copy" >&2
    exit 1
fi

gzip -f "$db_copy"
db_copy="$db_copy.gz"

tar --exclude='*.bak*' --exclude='__pycache__' --exclude='homewizard.db*' \
    -czf "$archive" -C "$(dirname "$APP_DIR")" "$(basename "$APP_DIR")"

# Both forms: the old copies (.db) and the compressed ones (.db.gz).
find "$DEST_DIR" -type f \( -name 'homewizard_*.db' -o -name 'homewizard_*.db.gz' \) -mtime +"$RETENTION_DAYS" -delete
find "$DEST_DIR" -name 'opt_homewizard_*.tar.gz' -mtime +"$RETENTION_DAYS" -delete

echo "[BACKUP_OK] $db_copy $archive"
