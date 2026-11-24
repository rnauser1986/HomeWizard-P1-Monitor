import json
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

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
