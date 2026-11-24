"""Shared helpers for the tests. No real meter, network or Signal server is used."""
import importlib
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def fresh(module_name):
    """Import a module again so its in-memory alarm state starts empty."""
    module = importlib.import_module(module_name)
    return importlib.reload(module)


def make_db(rows=()):
    """Create a temporary database with the real schema. Return its path.

    Each row is a dict. Missing columns stay NULL. 'ts' is a datetime in UTC.
    """
    import collector

    path = os.path.join(tempfile.mkdtemp(prefix="hw-test-"), "test.db")
    collector.init_db(path).close()
    conn = sqlite3.connect(path)
    for row in rows:
        conn.execute(
            "INSERT INTO measurements (ts_utc, active_power_w, active_voltage_l1_v,"
            " active_voltage_l2_v, active_voltage_l3_v, active_current_l1_a,"
            " active_current_l2_a, active_current_l3_a, total_power_import_kwh, raw_json)"
            " VALUES (?,?,?,?,?,?,?,?,?, '{}')",
            (
                row["ts"].isoformat(),
                row.get("w"),
                row.get("v1"), row.get("v2"), row.get("v3"),
                row.get("a1"), row.get("a2"), row.get("a3"),
                row.get("kwh"),
            ),
        )
    conn.commit()
    conn.close()
    return path


class FakeClock:
    """A clock the test moves by hand. Replaces time.time()."""

    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def utc_now():
    return datetime.now(timezone.utc)


__all__ = ["fresh", "make_db", "FakeClock", "utc_now", "timedelta"]
