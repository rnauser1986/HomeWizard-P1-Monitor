import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta

from config import (
    DEFAULT_FIXED_SEK,
    DEFAULT_PRICE_AREA,
    DEFAULT_TARIFF_START_DATE,
    TARIFF_MAX_SLOT_AGE_HOURS as MAX_SLOT_AGE_HOURS,
    TARIFF_SYNC_COOLDOWN_SEC as SYNC_COOLDOWN_SEC,
)

USER_AGENT = "HomeWizard-P1-Monitor"
_last_sync_attempt = 0.0


def get_cfg(cur):
    """The price settings. Row 1 of tariff_config, or the defaults from .env when the table has no row."""
    r = cur.execute("SELECT * FROM tariff_config WHERE id=1").fetchone()
    if not r:
        return {
            "start_date": DEFAULT_TARIFF_START_DATE,
            "mode": "fixed",
            "interval_min": 60,
            "area": DEFAULT_PRICE_AREA,
            "fixed_sek": DEFAULT_FIXED_SEK,
            "add_sek": 0.0,
            "vat_pct": 25.0,
        }
    return dict(r)


def apply_tariff(base_sek, cfg):
    add = float(cfg.get("add_sek") or 0.0)
    vat = float(cfg.get("vat_pct") or 0.0)
    return round((float(base_sek) + add) * (1.0 + vat / 100.0), 4)


def fetch_day_spots(db_path, area, day_str):
    y, m, d = day_str.split("-")
    url = f"https://www.elprisetjustnu.se/api/v1/prices/{y}/{m}-{d}_{area}.json"
    # The price service answers 403 to the default Python name, so the request names the program.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            items = json.loads(resp.read().decode())
    except Exception as e:
        # At most two lines in each HW_TARIFF_SYNC_COOLDOWN. Tomorrow's prices are not there before the afternoon.
        print(f"[SPOT_ERR] {day_str} {area}: {type(e).__name__}: {e}", flush=True)
        return

    if not isinstance(items, list):
        print(f"[SPOT_ERR] {day_str} {area}: the answer is not a list", flush=True)
        return

    c = sqlite3.connect(db_path, timeout=10.0)
    try:
        for it in items:
            ts = str(it.get("time_start", ""))[:16].replace("T", " ")
            val = it.get("SEK_per_kWh")
            if ts and val is not None:
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO spot_prices(slot_key, sek_per_kwh) VALUES(?,?)",
                    (ts, fval),
                )
        c.commit()
    finally:
        c.close()


def sync_spots(db_path, area, force=False):
    """Fetch the spot prices of today and tomorrow. At most once in SYNC_COOLDOWN_SEC, unless forced."""
    global _last_sync_attempt
    now_ts = time.time()
    if not force and (now_ts - _last_sync_attempt) < SYNC_COOLDOWN_SEC:
        return
    _last_sync_attempt = now_ts

    now = datetime.now()
    for d in [now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")]:
        fetch_day_spots(db_path, area, d)


def _lookup_slot(cur, key, target_dt):
    """The newest spot price at or before the slot. None when there is none, or it is older than MAX_SLOT_AGE_HOURS."""
    row = cur.execute(
        "SELECT slot_key, sek_per_kwh FROM spot_prices WHERE slot_key<=? ORDER BY slot_key DESC LIMIT 1",
        (key,),
    ).fetchone()
    if not row:
        return None
    try:
        slot_dt = datetime.strptime(str(row["slot_key"])[:16], "%Y-%m-%d %H:%M")
        age_hours = (target_dt - slot_dt).total_seconds() / 3600.0
        if age_hours > MAX_SLOT_AGE_HOURS:
            return None
    except ValueError:
        pass
    return float(row["sek_per_kwh"])


def effective_rate(cur, db_path, ts_local_str=None):
    """The price in SEK per kWh for a local time (default: now), with surcharge and VAT."""
    cfg = get_cfg(cur)
    if not ts_local_str:
        ts_local_str = cur.execute("SELECT strftime('%Y-%m-%d %H:%M','now','localtime') t").fetchone()["t"]
    if ts_local_str[:10] < str(cfg.get("start_date") or DEFAULT_TARIFF_START_DATE):
        return apply_tariff(cfg.get("fixed_sek") or DEFAULT_FIXED_SEK, cfg)
    if cfg.get("mode") != "dynamic":
        return apply_tariff(cfg.get("fixed_sek") or DEFAULT_FIXED_SEK, cfg)

    step = 15 if int(cfg.get("interval_min") or 60) == 15 else 60
    dt = datetime.strptime(ts_local_str[:16], "%Y-%m-%d %H:%M")
    slot_dt = dt.replace(minute=(dt.minute // 15) * 15 if step == 15 else 0)
    key = slot_dt.strftime("%Y-%m-%d %H:%M")

    base = _lookup_slot(cur, key, slot_dt)
    if base is None:
        sync_spots(db_path, cfg.get("area") or DEFAULT_PRICE_AREA)
        chk_con = sqlite3.connect(db_path, timeout=10.0)
        chk_con.row_factory = sqlite3.Row
        try:
            base = _lookup_slot(chk_con.cursor(), key, slot_dt)
        finally:
            chk_con.close()

    if base is None:
        base = float(cfg.get("fixed_sek") or DEFAULT_FIXED_SEK)
    return apply_tariff(base, cfg)
