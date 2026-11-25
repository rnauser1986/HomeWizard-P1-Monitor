import sqlite3
import time
from datetime import datetime, timedelta

from config import (
    PQ_CACHE_TTL_SEC as CACHE_TTL_SEC,
    PQ_DV_WARN_V as DV_WARN_V,
    PQ_POWER_PEAK_W as POWER_PEAK_W,
)

_CACHE = {}

DV_EXPR = """CASE
    WHEN (
        (CASE WHEN active_voltage_l1_v IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN active_voltage_l2_v IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN active_voltage_l3_v IS NOT NULL THEN 1 ELSE 0 END)
    ) < 2 THEN NULL
    ELSE (
        MAX(COALESCE(active_voltage_l1_v, -999999), COALESCE(active_voltage_l2_v, -999999), COALESCE(active_voltage_l3_v, -999999))
        - MIN(COALESCE(active_voltage_l1_v, 999999), COALESCE(active_voltage_l2_v, 999999), COALESCE(active_voltage_l3_v, 999999))
    )
END"""


def _bucket_expr(hours: int, col="ts_utc"):
    """The bucket size in minutes and the SQL that puts a time (the column col) in its bucket."""
    if hours <= 24:
        return 1, f"strftime('%Y-%m-%dT%H:%M:00Z', {col})"
    if hours <= 72:
        return 5, f"strftime('%Y-%m-%dT%H:', {col}) || printf('%02d:00Z', (CAST(strftime('%M', {col}) AS INTEGER) / 5) * 5)"
    if hours <= 168:
        return 15, f"strftime('%Y-%m-%dT%H:', {col}) || printf('%02d:00Z', (CAST(strftime('%M', {col}) AS INTEGER) / 15) * 15)"
    return 60, f"strftime('%Y-%m-%dT%H:00:00Z', {col})"


def _cut_off(c, hrs, at, minute=False):
    """The start of the window as text in the format of ts_utc. With minute=True it is rounded down to a whole minute."""
    seconds = "00" if minute else "%S"
    return c.execute(f"SELECT strftime('%Y-%m-%dT%H:%M:{seconds}', ?, ?)", (at, f"-{hrs} hours")).fetchone()[0]


def compute_from_rows(c, hrs, at="now"):
    """The whole answer, counted from the rows of measurements. The reference: the minute table must give the same."""
    bucket_min, bucket_sql = _bucket_expr(hrs)
    cut = _cut_off(c, hrs, at)
    c.row_factory = sqlite3.Row
    cur = c.cursor()
    summary = dict(cur.execute(f"""
        SELECT ROUND(MIN(active_voltage_l1_v), 1) AS min_v1, ROUND(MAX(active_voltage_l1_v), 1) AS max_v1, ROUND(MAX(active_current_l1_a), 1) AS max_a1,
               ROUND(MIN(active_voltage_l2_v), 1) AS min_v2, ROUND(MAX(active_voltage_l2_v), 1) AS max_v2, ROUND(MAX(active_current_l2_a), 1) AS max_a2,
               ROUND(MIN(active_voltage_l3_v), 1) AS min_v3, ROUND(MAX(active_voltage_l3_v), 1) AS max_v3, ROUND(MAX(active_current_l3_a), 1) AS max_a3,
               ROUND(MAX(active_power_w), 0) AS max_w, ROUND(MAX({DV_EXPR}), 1) AS max_dv
        FROM measurements WHERE ts_utc >= ?
    """, (cut,)).fetchone())
    w_row = cur.execute("SELECT ts_utc FROM measurements WHERE ts_utc >= ? AND active_power_w IS NOT NULL ORDER BY active_power_w DESC, id DESC LIMIT 1", (cut,)).fetchone()
    dv_row = cur.execute(f"SELECT ts_utc FROM measurements WHERE ts_utc >= ? AND (active_voltage_l1_v IS NOT NULL OR active_voltage_l2_v IS NOT NULL OR active_voltage_l3_v IS NOT NULL) ORDER BY {DV_EXPR} DESC, id DESC LIMIT 1", (cut,)).fetchone()
    summary["max_w_ts"] = w_row["ts_utc"] if w_row else None
    summary["max_dv_ts"] = dv_row["ts_utc"] if dv_row else None
    aggregates = dict(cur.execute(f"""
        SELECT COUNT(*) AS samples_cnt,
               SUM(CASE WHEN active_voltage_l1_v < 215 OR active_voltage_l2_v < 215 OR active_voltage_l3_v < 215 THEN 1 ELSE 0 END) AS low_v_cnt,
               SUM(CASE WHEN active_voltage_l1_v > 245 OR active_voltage_l2_v > 245 OR active_voltage_l3_v > 245 THEN 1 ELSE 0 END) AS high_v_cnt,
               SUM(CASE WHEN active_current_l1_a >= 20 THEN 1 ELSE 0 END) AS high_a_l1_cnt,
               SUM(CASE WHEN active_current_l2_a >= 20 THEN 1 ELSE 0 END) AS high_a_l2_cnt,
               SUM(CASE WHEN active_current_l3_a >= 20 THEN 1 ELSE 0 END) AS high_a_l3_cnt,
               SUM(CASE WHEN active_current_l1_a >= 20 OR active_current_l2_a >= 20 OR active_current_l3_a >= 20 THEN 1 ELSE 0 END) AS high_a_cnt,
               SUM(CASE WHEN active_power_w >= ? THEN 1 ELSE 0 END) AS high_w_cnt,
               SUM(CASE WHEN {DV_EXPR} >= ? THEN 1 ELSE 0 END) AS high_dv_cnt
        FROM measurements WHERE ts_utc >= ?
    """, (POWER_PEAK_W, DV_WARN_V, cut)).fetchone())
    aggregates["power_threshold_w"] = POWER_PEAK_W
    aggregates["dv_threshold_v"] = DV_WARN_V
    series = [dict(r) for r in cur.execute(f"""
        SELECT {bucket_sql} AS bucket_utc,
               ROUND(AVG(active_voltage_l1_v), 1) AS v1, ROUND(AVG(active_voltage_l2_v), 1) AS v2, ROUND(AVG(active_voltage_l3_v), 1) AS v3,
               ROUND(MAX(active_current_l1_a), 1) AS a1_max, ROUND(MAX(active_current_l2_a), 1) AS a2_max, ROUND(MAX(active_current_l3_a), 1) AS a3_max,
               ROUND(MAX(active_power_w), 0) AS w_max, ROUND(MAX({DV_EXPR}), 1) AS dv_max
        FROM measurements WHERE ts_utc >= ? GROUP BY bucket_utc ORDER BY bucket_utc ASC
    """, (cut,)).fetchall()]
    events = [dict(r) for r in cur.execute(f"""
        SELECT substr(ts_utc, 1, 19) AS ts_utc,
               ROUND(MIN(active_voltage_l1_v), 1) AS v1, ROUND(MIN(active_voltage_l2_v), 1) AS v2, ROUND(MIN(active_voltage_l3_v), 1) AS v3,
               ROUND(MAX(active_current_l1_a), 1) AS a1, ROUND(MAX(active_current_l2_a), 1) AS a2, ROUND(MAX(active_current_l3_a), 1) AS a3,
               ROUND(MAX(active_power_w), 0) AS w, ROUND(MAX({DV_EXPR}), 1) AS dv
        FROM measurements WHERE ts_utc >= ?
          AND (active_current_l1_a >= 20 OR active_current_l2_a >= 20 OR active_current_l3_a >= 20
               OR active_voltage_l1_v < 215 OR active_voltage_l2_v < 215 OR active_voltage_l3_v < 215
               OR active_voltage_l1_v > 245 OR active_voltage_l2_v > 245 OR active_voltage_l3_v > 245
               OR active_power_w >= ? OR {DV_EXPR} >= ?)
        GROUP BY substr(ts_utc, 1, 19) ORDER BY ts_utc DESC LIMIT 1000
    """, (cut, POWER_PEAK_W, DV_WARN_V)).fetchall()]
    return {
        "hours": hrs,
        "bucket_minutes": bucket_min,
        "summary": summary,
        "aggregates": aggregates,
        "series": series,
        "events": events,
    }


# ---- The minute table ----------------------------------------------------------------------------------------------------
# One row for each minute with what the power quality page needs: the extremes, the sums for the averages and the counts of
# samples over each limit. The page then reads some thousand rows instead of every sample of the window. The raw rows stay the
# truth: the table is made from them, and the rows version above must give the same numbers (the tests compare them).

MINUTE_TABLE = "measurements_minute"
META_TABLE = "measurements_minute_meta"

_MINUTE_DDL = f"""
CREATE TABLE IF NOT EXISTS {MINUTE_TABLE} (
    minute_utc TEXT PRIMARY KEY,
    n INTEGER NOT NULL,
    min_v1 REAL, max_v1 REAL, min_v2 REAL, max_v2 REAL, min_v3 REAL, max_v3 REAL,
    max_a1 REAL, max_a2 REAL, max_a3 REAL,
    max_w REAL, w_ts TEXT, max_dv REAL, dv_ts TEXT,
    sum_v1 REAL, sum_v2 REAL, sum_v3 REAL, n_v1 INTEGER, n_v2 INTEGER, n_v3 INTEGER,
    low_v_n INTEGER, high_v_n INTEGER, high_a1_n INTEGER, high_a2_n INTEGER, high_a3_n INTEGER, high_a_n INTEGER,
    high_w_n INTEGER, high_dv_n INTEGER
) WITHOUT ROWID
"""

_MINUTE_COLUMNS = (
    "minute_utc, n, min_v1, max_v1, min_v2, max_v2, min_v3, max_v3, max_a1, max_a2, max_a3, "
    "max_w, w_ts, max_dv, dv_ts, sum_v1, sum_v2, sum_v3, n_v1, n_v2, n_v3, "
    "low_v_n, high_v_n, high_a1_n, high_a2_n, high_a3_n, high_a_n, high_w_n, high_dv_n"
)

# The rows with a time from :a up to (not with) :b, grouped by minute. w_ts and dv_ts are the time of the sample with the
# highest power and the highest spread between phases (the latest one when two are equal), as the rows version finds them.
_MINUTE_FILL_SQL = f"""
WITH r AS (
    SELECT id, ts_utc, substr(ts_utc, 1, 16) || ':00' AS mk,
           active_voltage_l1_v AS v1, active_voltage_l2_v AS v2, active_voltage_l3_v AS v3,
           active_current_l1_a AS a1, active_current_l2_a AS a2, active_current_l3_a AS a3,
           active_power_w AS w, ({DV_EXPR}) AS dv,
           (active_voltage_l1_v IS NOT NULL OR active_voltage_l2_v IS NOT NULL OR active_voltage_l3_v IS NOT NULL) AS has_v
    FROM measurements WHERE ts_utc >= :a AND ts_utc < :b
), k AS (
    SELECT r.*,
           ROW_NUMBER() OVER (PARTITION BY mk ORDER BY (w IS NULL), w DESC, id DESC) AS rn_w,
           ROW_NUMBER() OVER (PARTITION BY mk ORDER BY (has_v = 0), (dv IS NULL), dv DESC, id DESC) AS rn_dv
    FROM r
)
INSERT OR REPLACE INTO {MINUTE_TABLE} ({_MINUTE_COLUMNS})
SELECT mk, COUNT(*), MIN(v1), MAX(v1), MIN(v2), MAX(v2), MIN(v3), MAX(v3), MAX(a1), MAX(a2), MAX(a3),
       MAX(w), MAX(CASE WHEN rn_w = 1 AND w IS NOT NULL THEN ts_utc END),
       MAX(dv), MAX(CASE WHEN rn_dv = 1 AND has_v = 1 THEN ts_utc END),
       SUM(v1), SUM(v2), SUM(v3), COUNT(v1), COUNT(v2), COUNT(v3),
       SUM(CASE WHEN v1 < 215 OR v2 < 215 OR v3 < 215 THEN 1 ELSE 0 END),
       SUM(CASE WHEN v1 > 245 OR v2 > 245 OR v3 > 245 THEN 1 ELSE 0 END),
       SUM(CASE WHEN a1 >= 20 THEN 1 ELSE 0 END), SUM(CASE WHEN a2 >= 20 THEN 1 ELSE 0 END), SUM(CASE WHEN a3 >= 20 THEN 1 ELSE 0 END),
       SUM(CASE WHEN a1 >= 20 OR a2 >= 20 OR a3 >= 20 THEN 1 ELSE 0 END),
       SUM(CASE WHEN w >= :peak THEN 1 ELSE 0 END), SUM(CASE WHEN dv >= :dvw THEN 1 ELSE 0 END)
FROM k GROUP BY mk
"""

_EVENT_WHERE = """(active_current_l1_a >= 20 OR active_current_l2_a >= 20 OR active_current_l3_a >= 20
               OR active_voltage_l1_v < 215 OR active_voltage_l2_v < 215 OR active_voltage_l3_v < 215
               OR active_voltage_l1_v > 245 OR active_voltage_l2_v > 245 OR active_voltage_l3_v > 245
               OR active_power_w >= :peak OR {dv} >= :dvw)"""


def _limits():
    return {"peak": POWER_PEAK_W, "dvw": DV_WARN_V}


def _limits_text():
    """How the limits are stored in the meta table. The counts in the minute table are only right for these values."""
    return {"power_peak_w": repr(float(POWER_PEAK_W)), "dv_warn_v": repr(float(DV_WARN_V))}


def ensure_minute_tables(c):
    c.execute(_MINUTE_DDL)
    c.execute(f"CREATE TABLE IF NOT EXISTS {META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def refresh_minutes(c, start, end):
    """Make the minute rows again from the samples with start <= ts_utc < end (text bounds, minute aligned, in ts_utc format)."""
    c.execute(f"DELETE FROM {MINUTE_TABLE} WHERE minute_utc >= ? AND minute_utc < ?", (start, end))
    c.execute(_MINUTE_FILL_SQL, {"a": start, "b": end, **_limits()})


def update_minute(c, ts_utc):
    """After a sample is stored: make the row of its minute again. The caller handles an error and the commit."""
    key = ts_utc[:16] + ":00"
    nxt = (datetime.strptime(key, "%Y-%m-%dT%H:%M:%S") + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    refresh_minutes(c, key, nxt)
    c.commit()


def sync_minutes(c):
    """At the start: make the minute table, and fill it from the samples when it is empty, behind or made for other limits.

    Minute rows older than the oldest sample are kept (the samples may be deleted one day, the statistics stay).
    Returns the number of samples it counted again, 0 when the table was in order."""
    ensure_minute_tables(c)
    meta = dict(c.execute(f"SELECT key, value FROM {META_TABLE}").fetchall())
    want = _limits_text()
    first = c.execute("SELECT MIN(ts_utc) FROM measurements").fetchone()[0]
    done = 0
    if first is not None:
        start = first[:16] + ":00"
        total = c.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        covered = c.execute(f"SELECT COALESCE(SUM(n), 0) FROM {MINUTE_TABLE} WHERE minute_utc >= ?", (start,)).fetchone()[0]
        if covered != total or any(meta.get(k) != v for k, v in want.items()):
            refresh_minutes(c, start, "9999")
            done = total
    for k, v in want.items():
        c.execute(f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES (?, ?)", (k, v))
    c.commit()
    return done


def minutes_ready(c):
    """True when the page may read the minute table: it exists, it is made for the limits in use and it has the newest minute.

    Otherwise the page counts from the samples, as before, so a missing or old table never gives a wrong answer."""
    try:
        meta = dict(c.execute(f"SELECT key, value FROM {META_TABLE}").fetchall())
        newest_sample = c.execute("SELECT MAX(ts_utc) FROM measurements").fetchone()[0]
        newest_minute = c.execute(f"SELECT MAX(minute_utc) FROM {MINUTE_TABLE}").fetchone()[0]
    except sqlite3.OperationalError:
        return False
    if any(meta.get(k) != v for k, v in _limits_text().items()):
        return False
    return bool(newest_sample) and newest_minute == newest_sample[:16] + ":00"


def compute_from_minutes(c, hrs, at="now"):
    """The same answer as compute_from_rows, counted from the minute table.

    The window starts at a whole minute (the minute that holds the cut-off), where the rows version starts at the second."""
    bucket_min, bucket_sql = _bucket_expr(hrs, "minute_utc")
    cut = _cut_off(c, hrs, at, minute=True)
    c.row_factory = sqlite3.Row
    cur = c.cursor()
    summary = dict(cur.execute(f"""
        SELECT ROUND(MIN(min_v1), 1) AS min_v1, ROUND(MAX(max_v1), 1) AS max_v1, ROUND(MAX(max_a1), 1) AS max_a1,
               ROUND(MIN(min_v2), 1) AS min_v2, ROUND(MAX(max_v2), 1) AS max_v2, ROUND(MAX(max_a2), 1) AS max_a2,
               ROUND(MIN(min_v3), 1) AS min_v3, ROUND(MAX(max_v3), 1) AS max_v3, ROUND(MAX(max_a3), 1) AS max_a3,
               ROUND(MAX(max_w), 0) AS max_w, ROUND(MAX(max_dv), 1) AS max_dv
        FROM {MINUTE_TABLE} WHERE minute_utc >= ?
    """, (cut,)).fetchone())
    w_row = cur.execute(f"SELECT w_ts FROM {MINUTE_TABLE} WHERE minute_utc >= ? AND max_w IS NOT NULL ORDER BY max_w DESC, minute_utc DESC LIMIT 1", (cut,)).fetchone()
    dv_row = cur.execute(f"SELECT dv_ts FROM {MINUTE_TABLE} WHERE minute_utc >= ? AND dv_ts IS NOT NULL ORDER BY max_dv DESC, minute_utc DESC LIMIT 1", (cut,)).fetchone()
    summary["max_w_ts"] = w_row["w_ts"] if w_row else None
    summary["max_dv_ts"] = dv_row["dv_ts"] if dv_row else None
    aggregates = dict(cur.execute(f"""
        SELECT COALESCE(SUM(n), 0) AS samples_cnt, SUM(low_v_n) AS low_v_cnt, SUM(high_v_n) AS high_v_cnt,
               SUM(high_a1_n) AS high_a_l1_cnt, SUM(high_a2_n) AS high_a_l2_cnt, SUM(high_a3_n) AS high_a_l3_cnt,
               SUM(high_a_n) AS high_a_cnt, SUM(high_w_n) AS high_w_cnt, SUM(high_dv_n) AS high_dv_cnt
        FROM {MINUTE_TABLE} WHERE minute_utc >= ?
    """, (cut,)).fetchone())
    aggregates["power_threshold_w"] = POWER_PEAK_W
    aggregates["dv_threshold_v"] = DV_WARN_V
    series = [dict(r) for r in cur.execute(f"""
        SELECT {bucket_sql} AS bucket_utc,
               ROUND(SUM(sum_v1) * 1.0 / SUM(n_v1), 1) AS v1, ROUND(SUM(sum_v2) * 1.0 / SUM(n_v2), 1) AS v2,
               ROUND(SUM(sum_v3) * 1.0 / SUM(n_v3), 1) AS v3,
               ROUND(MAX(max_a1), 1) AS a1_max, ROUND(MAX(max_a2), 1) AS a2_max, ROUND(MAX(max_a3), 1) AS a3_max,
               ROUND(MAX(max_w), 0) AS w_max, ROUND(MAX(max_dv), 1) AS dv_max
        FROM {MINUTE_TABLE} WHERE minute_utc >= ? GROUP BY bucket_utc ORDER BY bucket_utc ASC
    """, (cut,)).fetchall()]
    # The events keep their exact time. Only the minutes that hold one are looked up in the samples, the newest first.
    event_sql = f"""
        SELECT substr(ts_utc, 1, 19) AS ts_utc,
               ROUND(MIN(active_voltage_l1_v), 1) AS v1, ROUND(MIN(active_voltage_l2_v), 1) AS v2, ROUND(MIN(active_voltage_l3_v), 1) AS v3,
               ROUND(MAX(active_current_l1_a), 1) AS a1, ROUND(MAX(active_current_l2_a), 1) AS a2, ROUND(MAX(active_current_l3_a), 1) AS a3,
               ROUND(MAX(active_power_w), 0) AS w, ROUND(MAX({DV_EXPR}), 1) AS dv
        FROM measurements WHERE ts_utc >= :m AND ts_utc < strftime('%Y-%m-%dT%H:%M:00', :m, '+1 minute')
          AND {_EVENT_WHERE.format(dv=DV_EXPR)}
        GROUP BY substr(ts_utc, 1, 19) ORDER BY ts_utc DESC
    """
    minutes = [r[0] for r in cur.execute(f"""
        SELECT minute_utc FROM {MINUTE_TABLE}
        WHERE minute_utc >= ? AND (low_v_n > 0 OR high_v_n > 0 OR high_a_n > 0 OR high_w_n > 0 OR high_dv_n > 0)
        ORDER BY minute_utc DESC
    """, (cut,)).fetchall()]
    events = []
    for m in minutes:
        events.extend(dict(r) for r in cur.execute(event_sql, {"m": m, **_limits()}).fetchall())
        if len(events) >= 1000:
            break
    return {
        "hours": hrs,
        "bucket_minutes": bucket_min,
        "summary": summary,
        "aggregates": aggregates,
        "series": series,
        "events": events[:1000],
    }


def get_power_quality(db_path, hours):
    hrs = max(1, min(int(hours or 24), 720))
    cache_key = (db_path, hrs)
    now_mono = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and (now_mono - cached[0]) < CACHE_TTL_SEC:
        return cached[1]

    c = sqlite3.connect(db_path, timeout=5.0)
    try:
        c.execute("PRAGMA busy_timeout=5000;")
        payload = compute_from_minutes(c, hrs) if minutes_ready(c) else compute_from_rows(c, hrs)
    finally:
        c.close()
    _CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def get_power_quality_summary(db_path, hours):
    """Only the lowest and highest voltage and the highest current of each phase. The statistics page needs no more.

    It reads the same window as get_power_quality and rounds the same way, so the numbers are the same.
    It skips the series, the events, the counts and the spread between phases.
    """
    hrs = max(1, min(int(hours or 24), 720))
    cache_key = (db_path, hrs, "summary")
    now_mono = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and (now_mono - cached[0]) < CACHE_TTL_SEC:
        return cached[1]

    c = sqlite3.connect(db_path, timeout=5.0)
    try:
        c.execute("PRAGMA busy_timeout=5000;")
        c.row_factory = sqlite3.Row
        if minutes_ready(c):
            sql = f"""
                SELECT ROUND(MIN(min_v1), 1) AS min_v1, ROUND(MAX(max_v1), 1) AS max_v1, ROUND(MAX(max_a1), 1) AS max_a1,
                       ROUND(MIN(min_v2), 1) AS min_v2, ROUND(MAX(max_v2), 1) AS max_v2, ROUND(MAX(max_a2), 1) AS max_a2,
                       ROUND(MIN(min_v3), 1) AS min_v3, ROUND(MAX(max_v3), 1) AS max_v3, ROUND(MAX(max_a3), 1) AS max_a3
                FROM {MINUTE_TABLE} WHERE minute_utc >= ?
            """
            cut = _cut_off(c, hrs, "now", minute=True)
        else:
            sql = """
                SELECT ROUND(MIN(active_voltage_l1_v), 1) AS min_v1, ROUND(MAX(active_voltage_l1_v), 1) AS max_v1, ROUND(MAX(active_current_l1_a), 1) AS max_a1,
                       ROUND(MIN(active_voltage_l2_v), 1) AS min_v2, ROUND(MAX(active_voltage_l2_v), 1) AS max_v2, ROUND(MAX(active_current_l2_a), 1) AS max_a2,
                       ROUND(MIN(active_voltage_l3_v), 1) AS min_v3, ROUND(MAX(active_voltage_l3_v), 1) AS max_v3, ROUND(MAX(active_current_l3_a), 1) AS max_a3
                FROM measurements WHERE ts_utc >= ?
            """
            cut = _cut_off(c, hrs, "now")
        summary = dict(c.execute(sql, (cut,)).fetchone())
    finally:
        c.close()
    payload = {"hours": hrs, "summary": summary}
    _CACHE[cache_key] = (time.monotonic(), payload)
    return payload
