import json
import sqlite3
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import tariff_mod
from config import (
    API_HOST,
    API_PORT as PORT,
    DB_PATH as DB,
    DEFAULT_FIXED_SEK,
    HEALTH_MAX_AGE_SEC,
    PQ_MAX_HOURS,
    POLL_INTERVAL_SEC,
    UI_PATH as UI,
)
from pq_data import get_power_quality
from pq_page import PQ_HTML


def conn():
    c = sqlite3.connect(DB, timeout=5.0)
    c.execute("PRAGMA busy_timeout=5000;")
    c.row_factory = sqlite3.Row
    return c


def int_arg(qs, name, default, low, high):
    """Read a whole number from the query. A bad value gives the default. A big value is limited."""
    try:
        value = int((qs.get(name) or [str(default)])[0])
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def cur_keys(cur):
    row = cur.execute(
        "SELECT date('now','localtime') d, strftime('%Y-%W','now','localtime') w, "
        "strftime('%Y-%m','now','localtime') m, strftime('%Y','now','localtime') y"
    ).fetchone()
    return row["d"], row["w"], row["m"], row["y"]


def m_kwh(cur, where_sql, params=()):
    r = cur.execute(
        "SELECT ROUND(MAX(total_power_import_kwh)-MIN(total_power_import_kwh),3) v FROM measurements WHERE ("
        + where_sql
        + ") AND (raw_json IS NULL OR raw_json NOT LIKE '%source%csv_import%')",
        params,
    ).fetchone()
    return float(r["v"] or 0.0)


def h_map(cur, ptype):
    try:
        rows = cur.execute(
            "SELECT period_key, kwh FROM historical_kwh WHERE period_type=?",
            (ptype,),
        ).fetchall()
        return {str(r["period_key"]): float(r["kwh"] or 0.0) for r in rows}
    except sqlite3.OperationalError:
        return {}


def h_kwh(cur, ptype, key):
    try:
        r = cur.execute(
            "SELECT kwh FROM historical_kwh WHERE period_type=? AND period_key=?",
            (ptype, key),
        ).fetchone()
        return float(r["kwh"] or 0.0) if r else 0.0
    except sqlite3.OperationalError:
        return 0.0


def current_rate_sek(cur):
    try:
        return float(tariff_mod.effective_rate(cur, DB))
    except Exception:
        return float(DEFAULT_FIXED_SEK)


def _stats(c):
    cur = c.cursor()
    latest = cur.execute(
        "SELECT active_power_w, active_power_l1_w, active_power_l2_w, active_power_l3_w "
        "FROM measurements ORDER BY ts_utc DESC LIMIT 1"
    ).fetchone()
    d_key, w_key, m_key, y_key = cur_keys(cur)
    rate = current_rate_sek(cur)

    day_kwh = round(
        m_kwh(cur, "date(ts_utc,'localtime')=date('now','localtime')")
        + h_kwh(cur, "day", d_key),
        3,
    )
    wk_dates = [
        cur.execute(
            "SELECT date(date('now','localtime','weekday 0','-6 days'), '+' || ? || ' days') d",
            (i,),
        ).fetchone()["d"]
        for i in range(7)
    ]
    live_wk_dates = {
        r["d"]
        for r in cur.execute(
            "SELECT DISTINCT date(ts_utc,'localtime') d FROM measurements WHERE date(ts_utc,'localtime') BETWEEN ? AND ? AND (raw_json IS NULL OR raw_json NOT LIKE '%source%csv_import%')",
            (wk_dates[0], wk_dates[-1]),
        ).fetchall()
    }
    w_day_sum = sum(
        v for k, v in h_map(cur, "day").items() if k in wk_dates and k not in live_wk_dates
    )
    week_kwh = round(
        m_kwh(cur, "date(ts_utc,'localtime') BETWEEN date('now','localtime','weekday 0','-6 days') AND date('now','localtime','weekday 0')")
        + h_kwh(cur, "week", w_key)
        + w_day_sum,
        3,
    )
    day_hist = h_map(cur, "day")
    month_hist = h_map(cur, "month")
    year_hist = h_map(cur, "year")
    m_day_sum = sum(v for k, v in day_hist.items() if k[:7] == m_key)
    y_day_sum = sum(v for k, v in day_hist.items() if k[:4] == y_key)
    y_mon_sum = sum(v for k, v in month_hist.items() if k[:4] == y_key)

    month_kwh = round(
        m_kwh(cur, "strftime('%Y-%m',ts_utc,'localtime')=strftime('%Y-%m','now','localtime')")
        + month_hist.get(m_key, 0.0)
        + m_day_sum,
        3,
    )
    year_kwh = round(
        m_kwh(cur, "strftime('%Y',ts_utc,'localtime')=strftime('%Y','now','localtime')")
        + year_hist.get(y_key, 0.0)
        + y_mon_sum
        + y_day_sum,
        3,
    )

    now_w = int(latest["active_power_w"]) if latest and latest["active_power_w"] is not None else 0
    l1 = int(latest["active_power_l1_w"]) if latest and latest["active_power_l1_w"] is not None else 0
    l2 = int(latest["active_power_l2_w"]) if latest and latest["active_power_l2_w"] is not None else 0
    l3 = int(latest["active_power_l3_w"]) if latest and latest["active_power_l3_w"] is not None else 0

    return {
        "now_w": now_w,
        "l1_w": l1,
        "l2_w": l2,
        "l3_w": l3,
        "day_kwh": day_kwh,
        "day_sek": round(day_kwh * rate, 2),
        "week_kwh": week_kwh,
        "week_sek": round(week_kwh * rate, 2),
        "month_kwh": month_kwh,
        "month_sek": round(month_kwh * rate, 2),
        "year_kwh": year_kwh,
        "year_sek": round(year_kwh * rate, 2),
    }


def stats():
    c = conn()
    try:
        return _stats(c)
    finally:
        c.close()


def merge_sorted(rm, hm, rate, label_fn=None):
    out = []
    for k in sorted(set(rm) | set(hm)):
        v = round(rm.get(k, 0.0) + hm.get(k, 0.0), 3)
        label = label_fn(k) if label_fn else k
        out.append({"label": label, "date_lbl": label, "val": v, "sek": round(v * rate, 2)})
    return out


def month_label(ym):
    ms = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}
    x = ym.split("-")
    return f"{ms.get(x[1], x[1])} {x[0]}" if len(x) == 2 else ym


def build_phases(cur, p, qs):
    if p in ("now", "day"):
        if p == "now":
            d_sel = cur.execute("SELECT date('now','localtime') d").fetchone()["d"]
        else:
            off = int_arg(qs, "offset", 0, -3650, 0)
            d_sel = cur.execute("SELECT date('now','localtime',?||' days') d", (off,)).fetchone()["d"]
        rows = cur.execute(
            "SELECT strftime('%H:%M',ts_utc,'localtime') AS label,"
            "ROUND(AVG(active_voltage_l1_v),1) AS v1,ROUND(AVG(active_voltage_l2_v),1) AS v2,ROUND(AVG(active_voltage_l3_v),1) AS v3,"
            "ROUND(AVG(active_current_l1_a),2) AS a1,ROUND(AVG(active_current_l2_a),2) AS a2,ROUND(AVG(active_current_l3_a),2) AS a3 "
            "FROM measurements WHERE date(ts_utc,'localtime')=? "
            "GROUP BY strftime('%Y-%m-%d %H:%M',ts_utc,'localtime') ORDER BY label ASC",
            (d_sel,),
        ).fetchall()
        return [dict(r) for r in rows]
    if p == "week":
        off = int_arg(qs, "offset", 0, -520, 520)
        d0 = cur.execute("SELECT date('now','localtime','weekday 0',?||' days') d0", (-6 + off * 7,)).fetchone()["d0"]
        days = [cur.execute("SELECT date(?,'+'||?||' days') d", (d0, i)).fetchone()["d"] for i in range(7)]
        lbls = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        qrows = cur.execute(
            "SELECT date(ts_utc,'localtime') AS d,"
            "ROUND(AVG(active_voltage_l1_v),1) AS v1,ROUND(AVG(active_voltage_l2_v),1) AS v2,ROUND(AVG(active_voltage_l3_v),1) AS v3,"
            "ROUND(MAX(active_current_l1_a),2) AS a1,ROUND(MAX(active_current_l2_a),2) AS a2,ROUND(MAX(active_current_l3_a),2) AS a3 "
            "FROM measurements WHERE date(ts_utc,'localtime') BETWEEN ? AND date(?,'+6 days') GROUP BY d",
            (d0, d0),
        ).fetchall()
        rm = {r["d"]: dict(r) for r in qrows}
        out = []
        for i, d in enumerate(days):
            item = rm.get(d) or {}
            out.append({"label": lbls[i], "v1": item.get("v1"), "v2": item.get("v2"), "v3": item.get("v3"), "a1": item.get("a1"), "a2": item.get("a2"), "a3": item.get("a3")})
        return out
    if p == "month":
        off = int_arg(qs, "offset", 0, -3650, 0)
        yr = cur.execute("SELECT strftime('%Y',date('now','localtime','start of year',?||' years')) y", (off,)).fetchone()["y"]
        qrows = cur.execute(
            "SELECT strftime('%Y-%m',ts_utc,'localtime') AS m,"
            "ROUND(AVG(active_voltage_l1_v),1) AS v1,ROUND(AVG(active_voltage_l2_v),1) AS v2,ROUND(AVG(active_voltage_l3_v),1) AS v3,"
            "ROUND(MAX(active_current_l1_a),2) AS a1,ROUND(MAX(active_current_l2_a),2) AS a2,ROUND(MAX(active_current_l3_a),2) AS a3 "
            "FROM measurements WHERE strftime('%Y',ts_utc,'localtime')=? GROUP BY m",
            (yr,),
        ).fetchall()
        rm = {r["m"]: dict(r) for r in qrows}
        out = []
        for m in range(1, 13):
            ym_k = f"{yr}-{m:02d}"
            item = rm.get(ym_k) or {}
            out.append({"label": month_label(ym_k).split()[0], "v1": item.get("v1"), "v2": item.get("v2"), "v3": item.get("v3"), "a1": item.get("a1"), "a2": item.get("a2"), "a3": item.get("a3")})
        return out
    qrows = cur.execute(
        "SELECT strftime('%Y',ts_utc,'localtime') AS y,"
        "ROUND(AVG(active_voltage_l1_v),1) AS v1,ROUND(AVG(active_voltage_l2_v),1) AS v2,ROUND(AVG(active_voltage_l3_v),1) AS v3,"
        "ROUND(MAX(active_current_l1_a),2) AS a1,ROUND(MAX(active_current_l2_a),2) AS a2,ROUND(MAX(active_current_l3_a),2) AS a3 "
        "FROM measurements GROUP BY y ORDER BY y ASC"
    ).fetchall()
    return [dict(dict(r), label=r["y"]) for r in qrows]


def calc_standby(pts_list, rate):
    vals = sorted(float(x.get("val") or 0.0) for x in pts_list if x.get("val") is not None and float(x.get("val") or 0.0) > 0)
    if not vals:
        return 0, 0.0, 0.0
    idx = min(len(vals) - 1, int(len(vals) * 0.15))
    sb_w = int(round(vals[idx]))
    # One point is one minute (the points are minute averages), so 60 points are one hour.
    hours = len(vals) / 60.0
    sb_kwh = round((sb_w / 1000.0) * hours, 2)
    sb_sek = round(sb_kwh * rate, 2)
    return sb_w, sb_kwh, sb_sek


def calc_standby_from_w(cur, where_sql, params):
    rows = cur.execute(
        f"SELECT active_power_w FROM measurements WHERE active_power_w > 0 AND ({where_sql}) ORDER BY active_power_w ASC",
        params,
    ).fetchall()
    vals = [float(r["active_power_w"]) for r in rows if r["active_power_w"] is not None]
    if not vals:
        return 0, 0.0
    idx = min(len(vals) - 1, int(len(vals) * 0.15))
    sb_w = int(round(vals[idx]))
    # One row for each poll, so the hours come from the poll interval.
    hours = len(vals) * POLL_INTERVAL_SEC / 3600.0
    sb_kwh = round((sb_w / 1000.0) * hours, 2)
    return sb_w, sb_kwh


def _history(c, p, qs):
    cur = c.cursor()
    rate = current_rate_sek(cur)
    if p == "now":
        rows = cur.execute("SELECT strftime('%H:%M',ts_utc,'localtime') AS label,ROUND(AVG(active_power_w),0) AS val FROM measurements WHERE date(ts_utc,'localtime')=date('now','localtime') GROUP BY strftime('%Y-%m-%d %H:%M',ts_utc,'localtime') ORDER BY label ASC").fetchall()
        ph = build_phases(cur, p, qs)
        pts_out = [{"label": r["label"], "val": int(r["val"] or 0), "sek": round(((r["val"] or 0) / 1000.0) * rate, 2)} for r in rows]
        sb_w, sb_kwh, sb_sek = calc_standby(pts_out, rate)
        return {"period": "now", "chart": "area", "unit": "W", "standby_w": sb_w, "standby_kwh": sb_kwh, "standby_sek": sb_sek, "points": pts_out, "phases": ph}
    if p == "day":
        off = int_arg(qs, "offset", 0, -3650, 0)
        d_sel = cur.execute("SELECT date('now','localtime',?||' days') d", (off,)).fetchone()["d"]
        rows = cur.execute("SELECT strftime('%H:%M',ts_utc,'localtime') AS label,ROUND(AVG(active_power_w),0) AS val FROM measurements WHERE date(ts_utc,'localtime')=? GROUP BY strftime('%Y-%m-%d %H:%M',ts_utc,'localtime') ORDER BY label ASC", (d_sel,)).fetchall()
        dk, _, _, _ = cur_keys(cur)
        mk_d = m_kwh(cur, "date(ts_utc,'localtime')=?", (d_sel,))
        extra_h = h_kwh(cur, "day", dk) if off == 0 else (h_kwh(cur, "day", d_sel) if mk_d == 0.0 else 0.0)
        dkwh = round(mk_d + extra_h, 3)
        ph = build_phases(cur, p, qs)
        pts_out = [{"label": r["label"], "val": int(r["val"] or 0), "sek": round(((r["val"] or 0) / 1000.0) * rate, 2)} for r in rows]
        sb_w, sb_kwh, sb_sek = calc_standby(pts_out, rate)
        return {"period": "day", "chart": "area", "unit": "W", "offset": off, "date": d_sel, "total_kwh": dkwh, "total_sek": round(dkwh * rate, 2), "standby_w": sb_w, "standby_kwh": sb_kwh, "standby_sek": sb_sek, "points": pts_out, "phases": ph}
    if p == "week":
        off = int_arg(qs, "offset", 0, -520, 520)
        d0 = cur.execute("SELECT date('now','localtime','weekday 0',?||' days') d0", (-6 + off * 7,)).fetchone()["d0"]
        days = [cur.execute("SELECT date(?,'+'||?||' days') d", (d0, i)).fetchone()["d"] for i in range(7)]
        lbls = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        ms = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}
        rows = cur.execute("SELECT date(ts_utc,'localtime') d,ROUND(MAX(total_power_import_kwh)-MIN(total_power_import_kwh),3) v FROM measurements WHERE date(ts_utc,'localtime') BETWEEN ? AND date(?,'+6 days') AND (raw_json IS NULL OR raw_json NOT LIKE '%source%csv_import%') GROUP BY d", (d0, d0)).fetchall()
        rm = {r["d"]: float(r["v"] or 0.0) for r in rows}
        hm = h_map(cur, "day")
        wkwh = round(m_kwh(cur, "date(ts_utc,'localtime') BETWEEN ? AND date(?,'+6 days')", (d0, d0)) + sum(hm.get(d, 0.0) for d in days), 3)
        ph = build_phases(cur, p, qs)
        pts = [{"label": lbls[i], "date_lbl": f"{lbls[i]} {int(d.split('-')[2])} {ms.get(d.split('-')[1], d.split('-')[1])}", "val": round(rm.get(d, 0.0) + hm.get(d, 0.0), 3), "sek": round((rm.get(d, 0.0) + hm.get(d, 0.0)) * rate, 2)} for i, d in enumerate(days)]
        wk_no = date.fromisoformat(d0).isocalendar()[1]
        sb_w, sb_kwh = calc_standby_from_w(cur, "date(ts_utc,'localtime') BETWEEN ? AND date(?,'+6 days')", (d0, d0))
        return {"period": "week", "chart": "bar", "unit": "kWh", "offset": off, "week": wk_no, "week_label": f"week {wk_no}", "start_date": d0, "end_date": days[-1], "total_kwh": wkwh, "total_sek": round(wkwh * rate, 2), "standby_w": sb_w, "standby_kwh": sb_kwh, "points": pts, "phases": ph}
    if p == "month":
        off = int_arg(qs, "offset", 0, -3650, 0)
        yr_sel = cur.execute("SELECT strftime('%Y',date('now','localtime','start of year',?||' years')) y", (off,)).fetchone()["y"]
        rows = cur.execute("SELECT strftime('%Y-%m',ts_utc,'localtime') k,ROUND(MAX(total_power_import_kwh)-MIN(total_power_import_kwh),3) v FROM measurements WHERE strftime('%Y',ts_utc,'localtime')=? AND (raw_json IS NULL OR raw_json NOT LIKE '%source%csv_import%') GROUP BY k ORDER BY k", (yr_sel,)).fetchall()
        hm = h_map(cur, "month")
        for k, v in h_map(cur, "day").items():
            hm[k[:7]] = round(hm.get(k[:7], 0.0) + v, 3)
        totals = {}
        for r in rows:
            totals[r["k"]] = round(totals.get(r["k"], 0.0) + float(r["v"] or 0.0), 3)
        for k, v in hm.items():
            if k.startswith(yr_sel + "-"):
                totals[k] = round(totals.get(k, 0.0) + float(v or 0.0), 3)
        pts = []
        for m in range(1, 13):
            ym_k = f"{yr_sel}-{m:02d}"
            val = round(totals.get(ym_k, 0.0), 3)
            pts.append({"label": month_label(ym_k), "date_lbl": month_label(ym_k), "val": val, "sek": round(val * rate, 2)})
        mkwh = round(sum(x["val"] for x in pts), 3)
        ph = build_phases(cur, p, qs)
        sb_w, sb_kwh = calc_standby_from_w(cur, "strftime('%Y',ts_utc,'localtime')=?", (yr_sel,))
        return {"period": "month", "chart": "bar", "unit": "kWh", "offset": off, "year": yr_sel, "month_label": yr_sel, "total_kwh": mkwh, "total_sek": round(mkwh * rate, 2), "standby_w": sb_w, "standby_kwh": sb_kwh, "points": pts, "phases": ph}
    if p == "year":
        rows = cur.execute("SELECT strftime('%Y',ts_utc,'localtime') k,ROUND(MAX(total_power_import_kwh)-MIN(total_power_import_kwh),3) v FROM measurements WHERE (raw_json IS NULL OR raw_json NOT LIKE '%source%csv_import%') GROUP BY k ORDER BY k").fetchall()
        ym = h_map(cur, "year")
        for k, v in h_map(cur, "month").items():
            ym[k[:4]] = round(ym.get(k[:4], 0.0) + v, 3)
        for k, v in h_map(cur, "day").items():
            ym[k[:4]] = round(ym.get(k[:4], 0.0) + v, 3)
        pts = merge_sorted({r["k"]: float(r["v"] or 0.0) for r in rows}, ym, rate)
        ph = build_phases(cur, p, qs)
        sb_w, sb_kwh = calc_standby_from_w(cur, "1=1", ())
        return {"period": "year", "chart": "bar", "unit": "kWh", "standby_w": sb_w, "standby_kwh": sb_kwh, "points": pts, "phases": ph}
    return {"period": p, "chart": "bar", "unit": "kWh", "points": []}


def history(p, qs):
    c = conn()
    try:
        return _history(c, p, qs)
    finally:
        c.close()


class H(BaseHTTPRequestHandler):
    def send_bytes(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self.route()
        except Exception as e:
            # A bad request must not end the thread without an answer.
            print(f"[HTTP_ERR] {self.path.split('?')[0]}: {type(e).__name__}: {e}", flush=True)
            try:
                self.send_bytes(500, "application/json", b'{"error":"internal error"}')
            except Exception:
                pass

    def route(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        if path == "/":
            with open(UI, "rb") as f:
                self.send_bytes(200, "text/html; charset=utf-8", f.read())
        elif path == "/stats":
            self.send_bytes(200, "application/json", json.dumps(stats()).encode())
        elif path == "/power-quality":
            self.send_bytes(200, "text/html; charset=utf-8", PQ_HTML.encode())
        elif path == "/api/power-quality":
            hrs = int_arg(qs, "hours", 24, 1, PQ_MAX_HOURS)
            self.send_bytes(200, "application/json", json.dumps(get_power_quality(DB, hrs)).encode())
        elif path == "/health":
            try:
                c = conn()
                try:
                    row = c.execute(
                        "SELECT MAX(ts_utc) AS latest_utc, "
                        "(strftime('%s','now') - strftime('%s', MAX(ts_utc))) AS age_sec "
                        "FROM measurements"
                    ).fetchone()
                finally:
                    c.close()
                latest_utc = row["latest_utc"] if row else None
                age_sec = int(row["age_sec"]) if row and row["age_sec"] is not None else None
                max_age_sec = HEALTH_MAX_AGE_SEC
                if latest_utc is None or age_sec is None:
                    status = "no_data"
                    ok = False
                elif age_sec > max_age_sec:
                    status = "stale_data"
                    ok = False
                else:
                    status = "ok"
                    ok = True
                payload = {
                    "ok": ok,
                    "status": status,
                    "latest_utc": latest_utc,
                    "age_sec": age_sec,
                    "max_age_sec": max_age_sec,
                }
                self.send_bytes(200 if ok else 503, "application/json", json.dumps(payload).encode())
            except Exception as e:
                payload = {"ok": False, "status": "db_error", "error": str(e)}
                self.send_bytes(503, "application/json", json.dumps(payload).encode())
        elif path == "/history":
            per = (qs.get("period", ["now"])[0] or "now").lower()
            self.send_bytes(200, "application/json", json.dumps(history(per, qs)).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        return


if __name__ == "__main__":
    ThreadingHTTPServer((API_HOST, PORT), H).serve_forever()
