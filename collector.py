import json
import signal
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

from alarm_imbalance import check_imbalance
from alarm_phase_amp import check_phase_amp
from alarm_spike import check_spike
from alarm_stale import check_stale
from alarm_voltage import check_voltage
from config import (
    DB_PATH,
    EXAMPLE_METER_URL,
    HOMEWIZARD_URL,
    METER_TIMEOUT_SEC,
    POLL_INTERVAL_SEC,
    SIGNAL_FROM,
    SIGNAL_TO,
    SIGNAL_URL,
)


def init_db(db_path):
    """Open the database and create the tables. The caller closes the connection."""
    c = sqlite3.connect(db_path, timeout=5.0)
    try:
        _create_schema(c)
        normalize_timestamps(c)
    except BaseException:
        c.close()
        raise
    return c


def _create_schema(c):
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")
    c.execute("""
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        wifi_strength INTEGER,
        active_power_w REAL,
        active_power_l1_w REAL,
        active_power_l2_w REAL,
        active_power_l3_w REAL,
        active_voltage_l1_v REAL,
        active_voltage_l2_v REAL,
        active_voltage_l3_v REAL,
        active_current_a REAL,
        active_current_l1_a REAL,
        active_current_l2_a REAL,
        active_current_l3_a REAL,
        total_power_import_kwh REAL,
        total_power_export_kwh REAL,
        raw_json TEXT NOT NULL
    )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts_utc)")
    # The standby power is a percentile of the power values. With this index SQLite finds it without sorting every row.
    c.execute("CREATE INDEX IF NOT EXISTS idx_measurements_power ON measurements(active_power_w)")
    c.execute("""
    CREATE TABLE IF NOT EXISTS historical_kwh (
        period_type TEXT NOT NULL,
        period_key TEXT NOT NULL,
        kwh REAL NOT NULL,
        PRIMARY KEY (period_type, period_key)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS tariff_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        start_date TEXT,
        mode TEXT,
        interval_min INTEGER,
        area TEXT,
        fixed_sek REAL,
        add_sek REAL,
        vat_pct REAL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS spot_prices (
        slot_key TEXT PRIMARY KEY,
        sek_per_kwh REAL NOT NULL
    )
    """)
    c.commit()


def normalize_timestamps(c):
    """Rewrite a ts_utc without a "T" (for example "2026-11-19 19:15:00" from an import) to "2026-11-19T19:15:00+00:00".

    The period queries compare ts_utc as text. A space sorts before "T", so a row with a space lands in the wrong day at the
    edge of a period. The column holds UTC, so only the separator and the offset change. Returns the number of rows changed."""
    n = c.execute(
        "UPDATE measurements SET ts_utc = replace(ts_utc, ' ', 'T') || '+00:00' WHERE ts_utc NOT LIKE '%T%'"
    ).rowcount
    c.commit()
    if n:
        print(f"[START] collector: wrote {n} old timestamps in the same format as the new ones", flush=True)
    return n


def describe_start():
    """One line for the log at the start. It holds no address and no number."""
    signal_state = "on" if (SIGNAL_URL and SIGNAL_FROM and SIGNAL_TO) else "off"
    return f"[START] collector: poll every {POLL_INTERVAL_SEC} s, Signal {signal_state}, database {DB_PATH}"


def warn_if_not_set_up():
    """Say it in the log when the program runs with the example settings. A silent alarm is worse than a loud one."""
    if not (SIGNAL_URL and SIGNAL_FROM and SIGNAL_TO):
        print("[CONFIG] Signal is not set up. Alarms go to the log only.", flush=True)
    if HOMEWIZARD_URL == EXAMPLE_METER_URL:
        print("[CONFIG] HW_METER_URL is still the example address.", flush=True)


def poll_once(conn):
    """Read the meter once, store the reading and run the alarms."""
    with urllib.request.urlopen(HOMEWIZARD_URL, timeout=METER_TIMEOUT_SEC) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    ts_utc = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO measurements (
            ts_utc, wifi_strength,
            active_power_w, active_power_l1_w, active_power_l2_w, active_power_l3_w,
            active_voltage_l1_v, active_voltage_l2_v, active_voltage_l3_v,
            active_current_a, active_current_l1_a, active_current_l2_a, active_current_l3_a,
            total_power_import_kwh, total_power_export_kwh, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts_utc,
            data.get("wifi_strength"),
            data.get("active_power_w"),
            data.get("active_power_l1_w"),
            data.get("active_power_l2_w"),
            data.get("active_power_l3_w"),
            data.get("active_voltage_l1_v"),
            data.get("active_voltage_l2_v"),
            data.get("active_voltage_l3_v"),
            data.get("active_current_a"),
            data.get("active_current_l1_a"),
            data.get("active_current_l2_a"),
            data.get("active_current_l3_a"),
            data.get("total_power_import_kwh"),
            data.get("total_power_export_kwh"),
            json.dumps(data, separators=(",", ":")),
        ),
    )
    conn.commit()
    amps = (data.get("active_current_l1_a"), data.get("active_current_l2_a"), data.get("active_current_l3_a"))
    volts = (data.get("active_voltage_l1_v"), data.get("active_voltage_l2_v"), data.get("active_voltage_l3_v"))
    check_spike(*amps)
    check_imbalance(*amps)
    check_phase_amp(*amps)
    check_voltage(*volts)
    check_stale(DB_PATH)


def _stop(signum, frame):
    """Turn SIGTERM into a normal exit, so that main() closes the database."""
    sys.exit(0)


def main():
    conn = init_db(DB_PATH)
    try:
        print(describe_start(), flush=True)
        warn_if_not_set_up()
        while True:
            try:
                poll_once(conn)
            except Exception as e:
                print(f"[POLL_ERR] {e}", flush=True)
                check_stale(DB_PATH)

            time.sleep(POLL_INTERVAL_SEC)
    finally:
        conn.close()


if __name__ == "__main__":
    # systemd and Docker stop a program with SIGTERM. Python does not run `finally` for it by default.
    signal.signal(signal.SIGTERM, _stop)
    main()
