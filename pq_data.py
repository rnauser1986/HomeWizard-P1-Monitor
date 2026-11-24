import sqlite3
import time

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


def _bucket_expr(hours: int):
    if hours <= 24:
        return 1, "strftime('%Y-%m-%dT%H:%M:00Z', ts_utc)"
    if hours <= 72:
        return 5, "strftime('%Y-%m-%dT%H:', ts_utc) || printf('%02d:00Z', (CAST(strftime('%M', ts_utc) AS INTEGER) / 5) * 5)"
    if hours <= 168:
        return 15, "strftime('%Y-%m-%dT%H:', ts_utc) || printf('%02d:00Z', (CAST(strftime('%M', ts_utc) AS INTEGER) / 15) * 15)"
    return 60, "strftime('%Y-%m-%dT%H:00:00Z', ts_utc)"


def get_power_quality(db_path, hours):
    hrs = max(1, min(int(hours or 24), 720))
    cache_key = (db_path, hrs)
    now_mono = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and (now_mono - cached[0]) < CACHE_TTL_SEC:
        return cached[1]

    bucket_min, bucket_sql = _bucket_expr(hrs)
    mod = f"-{hrs} hours"
    c = sqlite3.connect(db_path, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000;")
    c.row_factory = sqlite3.Row
    cur = c.cursor()
    summary = dict(cur.execute(f"""
        SELECT ROUND(MIN(active_voltage_l1_v), 1) AS min_v1, ROUND(MAX(active_voltage_l1_v), 1) AS max_v1, ROUND(MAX(active_current_l1_a), 1) AS max_a1,
               ROUND(MIN(active_voltage_l2_v), 1) AS min_v2, ROUND(MAX(active_voltage_l2_v), 1) AS max_v2, ROUND(MAX(active_current_l2_a), 1) AS max_a2,
               ROUND(MIN(active_voltage_l3_v), 1) AS min_v3, ROUND(MAX(active_voltage_l3_v), 1) AS max_v3, ROUND(MAX(active_current_l3_a), 1) AS max_a3,
               ROUND(MAX(active_power_w), 0) AS max_w, ROUND(MAX({DV_EXPR}), 1) AS max_dv
        FROM measurements WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
    """, (mod,)).fetchone())
    w_row = cur.execute("SELECT ts_utc FROM measurements WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?) AND active_power_w IS NOT NULL ORDER BY active_power_w DESC, id DESC LIMIT 1", (mod,)).fetchone()
    dv_row = cur.execute(f"SELECT ts_utc FROM measurements WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?) AND (active_voltage_l1_v IS NOT NULL OR active_voltage_l2_v IS NOT NULL OR active_voltage_l3_v IS NOT NULL) ORDER BY {DV_EXPR} DESC, id DESC LIMIT 1", (mod,)).fetchone()
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
        FROM measurements WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
    """, (POWER_PEAK_W, DV_WARN_V, mod)).fetchone())
    aggregates["power_threshold_w"] = POWER_PEAK_W
    aggregates["dv_threshold_v"] = DV_WARN_V
    series = [dict(r) for r in cur.execute(f"""
        SELECT {bucket_sql} AS bucket_utc,
               ROUND(AVG(active_voltage_l1_v), 1) AS v1, ROUND(AVG(active_voltage_l2_v), 1) AS v2, ROUND(AVG(active_voltage_l3_v), 1) AS v3,
               ROUND(MAX(active_current_l1_a), 1) AS a1_max, ROUND(MAX(active_current_l2_a), 1) AS a2_max, ROUND(MAX(active_current_l3_a), 1) AS a3_max,
               ROUND(MAX(active_power_w), 0) AS w_max, ROUND(MAX({DV_EXPR}), 1) AS dv_max
        FROM measurements WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?) GROUP BY bucket_utc ORDER BY bucket_utc ASC
    """, (mod,)).fetchall()]
    events = [dict(r) for r in cur.execute(f"""
        SELECT substr(ts_utc, 1, 19) AS ts_utc,
               ROUND(MIN(active_voltage_l1_v), 1) AS v1, ROUND(MIN(active_voltage_l2_v), 1) AS v2, ROUND(MIN(active_voltage_l3_v), 1) AS v3,
               ROUND(MAX(active_current_l1_a), 1) AS a1, ROUND(MAX(active_current_l2_a), 1) AS a2, ROUND(MAX(active_current_l3_a), 1) AS a3,
               ROUND(MAX(active_power_w), 0) AS w, ROUND(MAX({DV_EXPR}), 1) AS dv
        FROM measurements WHERE ts_utc >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
          AND (active_current_l1_a >= 20 OR active_current_l2_a >= 20 OR active_current_l3_a >= 20
               OR active_voltage_l1_v < 215 OR active_voltage_l2_v < 215 OR active_voltage_l3_v < 215
               OR active_voltage_l1_v > 245 OR active_voltage_l2_v > 245 OR active_voltage_l3_v > 245
               OR active_power_w >= ? OR {DV_EXPR} >= ?)
        GROUP BY substr(ts_utc, 1, 19) ORDER BY ts_utc DESC LIMIT 1000
    """, (mod, POWER_PEAK_W, DV_WARN_V)).fetchall()]
    c.close()
    payload = {
        "hours": hrs,
        "bucket_minutes": bucket_min,
        "summary": summary,
        "aggregates": aggregates,
        "series": series,
        "events": events,
    }
    _CACHE[cache_key] = (time.monotonic(), payload)
    return payload
