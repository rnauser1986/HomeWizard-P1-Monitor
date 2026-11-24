import sqlite3
import time
from datetime import datetime, timezone

from config import (
    ALARM_STALE_COOLDOWN as COOLDOWN,
    ALARM_STALE_SEC as STALE_SEC,
)
from notify_signal import send_signal

_last_alert = 0.0
_was_stale = False


def check_stale(db_path):
    global _last_alert, _was_stale
    try:
        c = sqlite3.connect(db_path, timeout=5.0)
        try:
            c.execute("PRAGMA busy_timeout=5000;")
            row = c.execute("SELECT ts_utc FROM measurements ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
        if not row or not row[0]:
            return
        dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        now = time.time()
        if age > STALE_SEC:
            _was_stale = True
            if (now - _last_alert) >= COOLDOWN:
                _last_alert = now
                mins = int(age // 60)
                msg = f"HomeWizard warning: no new measurement data for {mins} min (latest: {row[0]})"
                print(f"[STALE] {msg}", flush=True)
                send_signal(msg)
        elif _was_stale:
            _was_stale = False
            msg = "HomeWizard info: new measurement data received again"
            print(f"[STALE_OK] {msg}", flush=True)
            send_signal(msg)
    except Exception as e:
        print(f"[STALE_ERR] {e}", flush=True)
