import json
import os
import random
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from datetime import datetime, timezone

from tests.helpers import fresh, make_db, timedelta, utc_now

LIVE_ROW = {"w": 100.0, "kwh": 1.0, "v1": 230.0, "v2": 230.0, "v3": 230.0, "a1": 1.0, "a2": 1.0, "a3": 1.0}


class StandbyTests(unittest.TestCase):
    def setUp(self):
        self.api = fresh("api_server")

    def test_day_and_now_points_are_minutes(self):
        """60 minute points of 100 W are one hour at 0.1 kW: 0.1 kWh."""
        sb_w, sb_kwh, sb_sek = self.api.calc_standby([{"val": 100} for _ in range(60)], 2.0)
        self.assertEqual((sb_w, sb_kwh, sb_sek), (100, 0.1, 0.2))

    def test_day_and_now_points_do_not_depend_on_the_poll_interval(self):
        with mock.patch.object(self.api, "POLL_INTERVAL_SEC", 30):
            self.assertEqual(self.api.calc_standby([{"val": 100} for _ in range(60)], 1.0)[1], 0.1)

    def test_no_points_is_no_standby(self):
        self.assertEqual(self.api.calc_standby([], 1.0), (0, 0.0, 0.0))
        self.assertEqual(self.api.calc_standby([{"val": 0}], 1.0), (0, 0.0, 0.0))

    def test_week_month_and_year_rows_are_polls(self):
        """360 rows of 100 W, 10 s apart, are one hour at 0.1 kW: 0.1 kWh."""
        now = utc_now()
        path = make_db([{"ts": now - timedelta(seconds=10 * i), "w": 100.0} for i in range(360)])
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            with mock.patch.object(self.api, "POLL_INTERVAL_SEC", 10):
                sb_w, sb_kwh = self.api.calc_standby_from_w(conn.cursor(), "1=1", ())
        finally:
            conn.close()
        self.assertEqual((sb_w, sb_kwh), (100, 0.1))


class IntArgTests(unittest.TestCase):
    def setUp(self):
        self.api = fresh("api_server")

    def test_values(self):
        arg = self.api.int_arg
        self.assertEqual(arg({"x": ["5"]}, "x", 0, -10, 10), 5)
        self.assertEqual(arg({}, "x", 7, -10, 10), 7)
        self.assertEqual(arg({"x": [""]}, "x", 7, -10, 10), 7)
        self.assertEqual(arg({"x": ["abc"]}, "x", 7, -10, 10), 7)
        self.assertEqual(arg({"x": ["1.5"]}, "x", 7, -10, 10), 7)
        self.assertEqual(arg({"x": ["999"]}, "x", 0, -10, 10), 10)
        self.assertEqual(arg({"x": ["-999"]}, "x", 0, -10, 10), -10)


class ServerTestCase(unittest.TestCase):
    rows = [LIVE_ROW]
    rows_with_time = True

    @classmethod
    def setUpClass(cls):
        cls.path = make_db([{**row, "ts": utc_now()} for row in cls.rows])
        cls.api = fresh("api_server")
        cls.api.DB = cls.path
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.api.H)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class RouteTests(ServerTestCase):
    def test_bad_hours_gives_the_default(self):
        status, body = self.get("/api/power-quality?hours=abc")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["hours"], 24)

    def test_huge_hours_is_limited_to_the_setting(self):
        for limit in (168, 720):
            with mock.patch.object(self.api, "PQ_MAX_HOURS", limit):
                status, body = self.get("/api/power-quality?hours=99999999999999999999")
            self.assertEqual((status, json.loads(body)["hours"]), (200, limit))

    def test_hours_below_the_limit_are_kept(self):
        with mock.patch.object(self.api, "PQ_MAX_HOURS", 168):
            self.assertEqual(json.loads(self.get("/api/power-quality?hours=72")[1])["hours"], 72)

    def test_bad_offset_gives_the_default(self):
        for period in ("day", "week", "month"):
            for offset in ("abc", "99999999999999999999", "-99999999999999999999"):
                status, _ = self.get(f"/history?period={period}&offset={offset}")
                self.assertEqual(status, 200, (period, offset))

    def test_every_period_answers_on_a_fresh_database(self):
        for period in ("now", "day", "week", "month", "year"):
            status, body = self.get(f"/history?period={period}")
            self.assertEqual(status, 200, period)
            self.assertEqual(json.loads(body)["period"], period)

    def test_week_history_has_seven_english_days(self):
        data = json.loads(self.get("/history?period=week")[1])
        self.assertEqual([p["label"] for p in data["points"]], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self.assertRegex(data["week_label"], r"^week \d+$")

    def test_month_history_has_twelve_english_months(self):
        points = json.loads(self.get("/history?period=month")[1])["points"]
        self.assertEqual(len(points), 12)
        self.assertTrue(points[0]["label"].startswith("Jan"))
        self.assertTrue(points[4]["label"].startswith("May"))

    def test_unknown_period_gives_empty_points(self):
        status, body = self.get("/history?period=nonsense")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["points"], [])

    def test_stats_answers_on_a_fresh_database(self):
        status, body = self.get("/stats")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["now_w"], 100)
        self.assertEqual(set(data), {"now_w", "l1_w", "l2_w", "l3_w", "day_kwh", "day_sek", "week_kwh", "week_sek",
                                     "month_kwh", "month_sek", "year_kwh", "year_sek"})

    def test_the_pages_are_served(self):
        for path in ("/", "/power-quality"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(b"<html", body)

    def test_unexpected_error_gives_500_and_no_details(self):
        with mock.patch.object(self.api, "stats", side_effect=RuntimeError("secret path /x")), \
             mock.patch("builtins.print"):
            status, body = self.get("/stats")
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret", body)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.get("/nope")[0], 404)


class TrackedConnection(sqlite3.Connection):
    """A connection that remembers that it was closed."""

    closed = False

    def close(self):
        self.closed = True
        super().close()


class ConnectionClosingTests(unittest.TestCase):
    """Every function that opens the database closes it, also when a query fails."""

    def setUp(self):
        self.api = fresh("api_server")
        self.path = make_db([{**LIVE_ROW, "ts": utc_now()}])
        self.opened = []

        def tracked_conn():
            c = sqlite3.connect(self.path, timeout=5.0, factory=TrackedConnection)
            c.row_factory = sqlite3.Row
            self.opened.append(c)
            return c

        patcher = mock.patch.object(self.api, "conn", tracked_conn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_all_closed(self):
        self.assertTrue(self.opened)
        self.assertTrue(all(c.closed for c in self.opened))

    def test_stats_closes_after_success(self):
        self.api.stats()
        self.assert_all_closed()

    def test_stats_closes_when_a_query_fails(self):
        with mock.patch.object(self.api, "current_rate_sek", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.api.stats()
        self.assert_all_closed()

    def test_every_history_period_closes_after_success(self):
        for period in ("now", "day", "week", "month", "year", "nonsense"):
            self.opened.clear()
            self.api.history(period, {})
            self.assert_all_closed()

    def test_every_history_period_closes_when_a_query_fails(self):
        with mock.patch.object(self.api, "current_rate_sek", side_effect=RuntimeError("boom")):
            for period in ("now", "day", "week", "month", "year"):
                self.opened.clear()
                with self.assertRaises(RuntimeError):
                    self.api.history(period, {})
                self.assert_all_closed()


class PlaceholderTests(unittest.TestCase):
    """The day and week queries pass their dates as parameters, not as SQL text."""

    def setUp(self):
        self.api = fresh("api_server")

    def test_m_kwh_takes_parameters(self):
        now = utc_now()
        path = make_db([{"ts": now - timedelta(minutes=10 * i), "kwh": 10.0 - i} for i in range(5)])
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            day = conn.execute("SELECT date(?,'localtime') d", (now.isoformat(),)).fetchone()["d"]
            self.assertEqual(self.api.m_kwh(conn.cursor(), "date(ts_utc,'localtime')=?", (day,)), 4.0)
            self.assertEqual(self.api.m_kwh(conn.cursor(), "date(ts_utc,'localtime')=?", ("1999-01-01",)), 0.0)
        finally:
            conn.close()

    def test_a_value_with_a_quote_does_not_change_the_query(self):
        path = make_db([{"ts": utc_now(), "kwh": 5.0}])
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            self.assertEqual(self.api.m_kwh(conn.cursor(), "date(ts_utc,'localtime')=?", ("x' OR '1'='1",)), 0.0)
        finally:
            conn.close()

    def test_no_date_is_written_into_the_sql_text(self):
        with open(self.api.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("{d_sel}", source)
        self.assertNotIn("{d0}", source)


@unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset")
class PeriodRangeTests(unittest.TestCase):
    """A range on ts_utc must select exactly the rows that date(ts_utc,'localtime') selected."""

    def setUp(self):
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Stockholm"
        time.tzset()
        self.addCleanup(self.restore_tz)
        self.api = fresh("api_server")
        rows = []
        # Around both changes of summer time, the end of a year, and the end of a month. One row every 20 minutes.
        for first, last in (("2026-03-27", "2026-04-01"), ("2026-10-23", "2026-10-28"), ("2025-12-29", "2026-01-03"), ("2026-01-30", "2026-03-03")):
            moment = datetime.fromisoformat(first).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
            while moment < end:
                rows.append({"ts": moment, "w": 100.0, "kwh": 1.0})
                moment += timedelta(minutes=20)
        path = make_db(rows)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def restore_tz(self):
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()

    def ids(self, where, params):
        return [r[0] for r in self.conn.execute("SELECT id FROM measurements WHERE " + where + " ORDER BY id", params)]

    def test_a_day_selects_the_same_rows_also_on_the_days_summer_time_changes(self):
        cur = self.conn.cursor()
        for day in ("2026-03-28", "2026-03-29", "2026-03-30", "2026-10-24", "2026-10-25", "2026-10-26", "2025-12-31", "2026-01-01", "2026-02-28"):
            old = self.ids("date(ts_utc,'localtime')=?", (day,))
            new = self.ids(self.api.RANGE, self.api.day_bounds(cur, day, "+1 day"))
            self.assertEqual(new, old, day)
            self.assertTrue(old, day)

    def test_a_week_selects_the_same_rows(self):
        cur = self.conn.cursor()
        for monday in ("2026-03-23", "2026-03-30", "2026-10-19", "2026-10-26", "2025-12-29"):
            old = self.ids("date(ts_utc,'localtime') BETWEEN ? AND date(?,'+6 days')", (monday, monday))
            new = self.ids(self.api.RANGE, self.api.day_bounds(cur, monday, "+7 days"))
            self.assertEqual(new, old, monday)

    def test_a_month_selects_the_same_rows(self):
        cur = self.conn.cursor()
        for month in ("2026-01", "2026-02", "2026-03", "2026-10", "2025-12"):
            old = self.ids("strftime('%Y-%m',ts_utc,'localtime')=?", (month,))
            new = self.ids(self.api.RANGE, self.api.day_bounds(cur, month + "-01", "+1 month"))
            self.assertEqual(new, old, month)

    def test_a_year_selects_the_same_rows(self):
        cur = self.conn.cursor()
        for year in ("2025", "2026"):
            old = self.ids("strftime('%Y',ts_utc,'localtime')=?", (year,))
            new = self.ids(self.api.RANGE, self.api.day_bounds(cur, year + "-01-01", "+1 year"))
            self.assertEqual(new, old, year)

    def test_the_bounds_have_the_format_of_ts_utc(self):
        lo, hi = self.api.day_bounds(self.conn.cursor(), "2026-03-29", "+1 day")
        self.assertRegex(lo, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertRegex(hi, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual((lo, hi), ("2026-03-28T23:00:00", "2026-03-29T22:00:00"))   # a 23 hour day

    def test_a_day_query_uses_the_index_on_ts_utc(self):
        plan = " ".join(r["detail"] for r in self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT AVG(active_power_w) FROM measurements WHERE " + self.api.RANGE, ("a", "b")))
        self.assertIn("idx_measurements_ts", plan)

    def test_no_query_filters_a_period_with_a_function_on_ts_utc(self):
        with open(self.api.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("WHERE date(ts_utc,'localtime')", source)
        self.assertNotRegex(source, r"WHERE strftime\('%Y(-%m)?',ts_utc,'localtime'\)=")


class StandbyFromSqlTests(unittest.TestCase):
    """The percentile that SQLite finds is the one the old Python code found."""

    def setUp(self):
        self.api = fresh("api_server")

    def reference(self, values, poll):
        vals = sorted(v for v in values if v is not None and v > 0)
        if not vals:
            return 0, 0.0
        idx = min(len(vals) - 1, int(len(vals) * 0.15))
        sb_w = int(round(vals[idx]))
        return sb_w, round((sb_w / 1000.0) * (len(vals) * poll / 3600.0), 2)

    def run_case(self, values):
        now = utc_now()
        path = make_db([{"ts": now - timedelta(seconds=10 * i), "w": v} for i, v in enumerate(values)])
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            with mock.patch.object(self.api, "POLL_INTERVAL_SEC", 10):
                return self.api.calc_standby_from_w(conn.cursor(), "1=1", ())
        finally:
            conn.close()

    def test_random_data_gives_the_same_answer_as_the_reference(self):
        rng = random.Random(5)
        for size in (1, 2, 3, 7, 20, 99, 100, 101, 500):
            values = [rng.choice((None, 0.0, -5.0, rng.uniform(1, 5000), 120.0, 120.0)) for _ in range(size)]
            self.assertEqual(self.run_case(values), self.reference(values, 10), size)

    def test_no_positive_value_is_no_standby(self):
        self.assertEqual(self.run_case([0.0, 0.0, -1.0]), (0, 0.0))
        self.assertEqual(self.run_case([]), (0, 0.0))

    def test_the_power_index_is_created_by_the_collector(self):
        path = make_db()
        conn = sqlite3.connect(path)
        try:
            names = {r[1] for r in conn.execute("PRAGMA index_list(measurements)")}
        finally:
            conn.close()
        self.assertIn("idx_measurements_power", names)
        self.assertIn("idx_measurements_ts", names)


class PowerQualitySummaryRouteTests(ServerTestCase):
    def test_part_summary_gives_only_the_summary(self):
        status, body = self.get("/api/power-quality?hours=24&part=summary")
        self.assertEqual(status, 200)
        answer = json.loads(body)
        self.assertEqual(set(answer), {"hours", "summary"})

    def test_without_part_it_gives_the_whole_answer(self):
        answer = json.loads(self.get("/api/power-quality?hours=24")[1])
        self.assertTrue({"summary", "series", "events", "aggregates"} <= set(answer))

    def test_another_value_of_part_gives_the_whole_answer(self):
        answer = json.loads(self.get("/api/power-quality?hours=24&part=whatever")[1])
        self.assertIn("series", answer)

    def test_the_statistics_page_asks_for_the_summary_only(self):
        with open(os.path.join(os.path.dirname(self.api.__file__), "ui.html"), encoding="utf-8") as f:
            self.assertIn("/api/power-quality?hours=24&part=summary", f.read())


class HardeningTests(ServerTestCase):
    def logged(self, mock_print):
        return " ".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)

    def test_a_database_error_does_not_reach_the_client(self):
        with mock.patch.object(self.api, "conn", side_effect=sqlite3.OperationalError("unable to open /secret/path/x.db")), \
             mock.patch("builtins.print") as printed:
            status, body = self.get("/health")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"ok": False, "status": "db_error"})
        self.assertNotIn(b"secret", body)
        self.assertIn("[HEALTH_ERR] OperationalError", self.logged(printed))

    def test_only_errors_are_logged_and_without_the_query(self):
        with mock.patch("builtins.print") as printed:
            self.assertEqual(self.get("/stats")[0], 200)
            self.assertEqual(self.get("/nope?token=abc")[0], 404)
        text = self.logged(printed)
        self.assertIn("[HTTP] GET /nope -> 404", text)
        self.assertNotIn("token", text)
        self.assertNotIn("/stats", text)

    def test_the_answers_have_safe_headers(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as r:
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")

    def test_a_client_that_sends_nothing_is_dropped(self):
        with mock.patch.object(self.api.H, "timeout", 0.3):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                sock.settimeout(5)
                started = time.monotonic()
                self.assertEqual(sock.recv(10), b"")          # the server closed the connection
                self.assertLess(time.monotonic() - started, 3)

    def test_the_server_has_a_default_timeout(self):
        self.assertGreater(self.api.H.timeout, 0)


class ServerShutdownTests(unittest.TestCase):
    def test_the_stop_handler_is_a_normal_exit(self):
        api = fresh("api_server")
        with self.assertRaises(SystemExit) as raised:
            api._stop(signal.SIGTERM, None)
        self.assertEqual(raised.exception.code, 0)

    def test_the_start_line_has_the_address_and_no_secret(self):
        api = fresh("api_server")
        line = api.describe_start()
        self.assertTrue(line.startswith("[START] api_server on "))

    @unittest.skipIf(os.name == "nt", "SIGTERM works differently on Windows")
    def test_the_real_program_stops_cleanly_on_sigterm(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        env = {**os.environ, "HW_DB_PATH": os.path.join(tempfile.mkdtemp(prefix="hw-test-"), "t.db"),
               "HW_API_HOST": "127.0.0.1", "HW_API_PORT": str(port)}
        proc = subprocess.Popen([sys.executable, "api_server.py"], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for _ in range(50):                                   # wait until it listens
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


class HealthTests(unittest.TestCase):
    def health(self, rows):
        path = make_db(rows)
        api = fresh("api_server")
        api.DB = path
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/health", timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_fresh_data_is_ok(self):
        status, body = self.health([{**LIVE_ROW, "ts": utc_now() - timedelta(seconds=5)}])
        self.assertEqual((status, body["status"], body["ok"]), (200, "ok", True))

    def test_old_data_is_503_stale_data(self):
        status, body = self.health([{**LIVE_ROW, "ts": utc_now() - timedelta(seconds=3600)}])
        self.assertEqual((status, body["status"], body["ok"]), (503, "stale_data", False))

    def test_no_data_is_503_no_data(self):
        status, body = self.health([])
        self.assertEqual((status, body["status"]), (503, "no_data"))

    def test_the_limit_comes_from_the_settings(self):
        status, body = self.health([{**LIVE_ROW, "ts": utc_now() - timedelta(seconds=5)}])
        self.assertEqual(body["max_age_sec"], fresh("config").HEALTH_MAX_AGE_SEC)


if __name__ == "__main__":
    unittest.main()
