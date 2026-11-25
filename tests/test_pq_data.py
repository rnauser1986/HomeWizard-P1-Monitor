import random
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.helpers import fresh, make_db, timedelta, utc_now

NORMAL = {"v1": 230.0, "v2": 230.0, "v3": 230.0, "a1": 1.0, "a2": 1.0, "a3": 1.0}


class PowerQualityTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("pq_data")

    def test_bucket_size_grows_with_the_window(self):
        sizes = [self.mod._bucket_expr(h)[0] for h in (6, 24, 72, 168, 720)]
        self.assertEqual(sizes, [1, 1, 5, 15, 60])

    def test_summary_counts_a_low_voltage_sample(self):
        path = make_db([{"ts": utc_now(), "w": 1000, **NORMAL, "v1": 210.0}])
        result = self.mod.get_power_quality(path, 24)
        self.assertEqual(result["aggregates"]["low_v_cnt"], 1)
        self.assertEqual(result["aggregates"]["high_v_cnt"], 0)

    def test_summary_counts_a_current_at_the_limit(self):
        path = make_db([{"ts": utc_now(), "w": 1000, **NORMAL, "a2": 20.0}])
        aggregates = self.mod.get_power_quality(path, 24)["aggregates"]
        self.assertEqual((aggregates["high_a_cnt"], aggregates["high_a_l2_cnt"], aggregates["high_a_l1_cnt"]), (1, 1, 0))

    def test_hours_are_limited_to_the_allowed_range(self):
        path = make_db()
        self.assertEqual(self.mod.get_power_quality(path, 0)["hours"], 24)
        self.assertEqual(self.mod.get_power_quality(path, 100000)["hours"], 720)

    def test_window_excludes_rows_older_than_the_window(self):
        """ts_utc is ISO text with a 'T'. The cut-off must use the same format."""
        now = utc_now()
        path = make_db([{"ts": now - timedelta(hours=30), "w": 1}, {"ts": now, "w": 1}])
        self.assertEqual(self.mod.get_power_quality(path, 24)["aggregates"]["samples_cnt"], 1)

    def test_every_part_of_the_answer_ignores_old_rows(self):
        """The old row has the extreme values. None of them may reach the summary, the series or the events."""
        now = utc_now()
        old = {"ts": now - timedelta(hours=30), "w": 20000.0, "v1": 150.0, "v2": 260.0, "v3": 230.0,
               "a1": 30.0, "a2": 1.0, "a3": 1.0}
        path = make_db([old, {"ts": now, "w": 500.0, **NORMAL}])
        result = self.mod.get_power_quality(path, 24)
        self.assertEqual(result["summary"]["max_w"], 500.0)
        self.assertEqual(result["summary"]["min_v1"], 230.0)
        self.assertEqual(result["summary"]["max_dv"], 0.0)
        self.assertEqual(result["summary"]["max_w_ts"][:10], now.isoformat()[:10])
        self.assertIsNotNone(result["summary"]["max_dv_ts"])
        self.assertEqual(sum(1 for _ in result["series"]), 1)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["aggregates"]["low_v_cnt"], 0)

    def test_window_keeps_rows_just_inside_the_window(self):
        now = utc_now()
        path = make_db([{"ts": now - timedelta(hours=23, minutes=50), "w": 1}])
        self.assertEqual(self.mod.get_power_quality(path, 24)["aggregates"]["samples_cnt"], 1)

    def test_empty_database_gives_an_empty_answer(self):
        result = self.mod.get_power_quality(make_db(), 24)
        self.assertEqual(result["series"], [])
        self.assertEqual(result["events"], [])
        self.assertIsNone(result["summary"]["max_dv"])

    def test_the_answer_is_cached_for_a_short_time(self):
        path = make_db([{"ts": utc_now(), "w": 1}])
        first = self.mod.get_power_quality(path, 24)
        self.assertIs(self.mod.get_power_quality(path, 24), first)


class SummaryOnlyTests(unittest.TestCase):
    """The light answer for the statistics page: the same numbers as the whole answer, with less work."""

    def setUp(self):
        self.mod = fresh("pq_data")
        self.mod._CACHE.clear()

    def rows(self):
        now = utc_now()
        return [{"ts": now - timedelta(minutes=5), "w": 4000.0, **NORMAL, "v1": 228.4, "a3": 12.3},
                {"ts": now - timedelta(minutes=1), "w": 9000.0, **NORMAL, "v2": 238.6, "a3": 27.8},
                {"ts": now - timedelta(hours=30), "w": 20000.0, **NORMAL, "v1": 150.0, "a1": 30.0}]

    def test_the_summary_has_the_same_numbers_as_the_whole_answer(self):
        path = make_db(self.rows())
        light = self.mod.get_power_quality_summary(path, 24)["summary"]
        whole = self.mod.get_power_quality(path, 24)["summary"]
        for key in ("min_v1", "max_v1", "max_a1", "min_v2", "max_v2", "max_a2", "min_v3", "max_v3", "max_a3"):
            self.assertEqual(light[key], whole[key], key)
        self.assertEqual((light["min_v1"], light["max_v2"], light["max_a3"]), (228.4, 238.6, 27.8))

    def test_it_ignores_rows_older_than_the_window(self):
        path = make_db(self.rows())
        self.assertGreater(self.mod.get_power_quality_summary(path, 24)["summary"]["min_v1"], 200)

    def test_it_holds_no_series_and_no_events(self):
        answer = self.mod.get_power_quality_summary(make_db(self.rows()), 24)
        self.assertEqual(set(answer), {"hours", "summary"})
        self.assertEqual(len(answer["summary"]), 9)

    def test_an_empty_database_gives_empty_numbers(self):
        summary = self.mod.get_power_quality_summary(make_db(), 24)["summary"]
        self.assertTrue(all(value is None for value in summary.values()))

    def test_hours_are_limited_and_the_answer_is_cached_apart_from_the_whole_one(self):
        path = make_db(self.rows())
        self.assertEqual(self.mod.get_power_quality_summary(path, 100000)["hours"], 720)
        first = self.mod.get_power_quality_summary(path, 24)
        self.assertIs(self.mod.get_power_quality_summary(path, 24), first)
        self.assertNotIn("series", first)
        self.assertIn("series", self.mod.get_power_quality(path, 24))

    def test_it_closes_the_database_also_when_the_query_fails(self):
        opened = []

        class Tracked(sqlite3.Connection):
            closed = False

            def close(self):
                self.closed = True
                super().close()

            def execute(self, sql, *args):
                if "SELECT ROUND" in sql:
                    raise sqlite3.OperationalError("the query failed")
                return super().execute(sql, *args)

        real_connect = sqlite3.connect

        def connect(path, timeout=5.0):
            c = real_connect(path, timeout=timeout, factory=Tracked)
            opened.append(c)
            return c

        path = make_db(self.rows())
        with mock.patch.object(self.mod.sqlite3, "connect", connect):
            with self.assertRaises(sqlite3.OperationalError):
                self.mod.get_power_quality_summary(path, 24)
        self.assertTrue(opened and all(c.closed for c in opened))


class ConnectionClosingTests(unittest.TestCase):
    """get_power_quality closes the database also when a query fails."""

    def setUp(self):
        self.mod = fresh("pq_data")
        self.mod._CACHE.clear()
        self.path = make_db([{"ts": utc_now(), "w": 1000, **NORMAL}])

    def run_with_tracking(self, fail):
        opened = []

        class Tracked(sqlite3.Connection):
            closed = False

            def close(self):
                self.closed = True
                super().close()

        real_connect = sqlite3.connect

        def connect(path, timeout=5.0):
            c = real_connect(path, timeout=timeout, factory=Tracked)
            opened.append(c)
            return c

        with mock.patch.object(self.mod.sqlite3, "connect", connect):
            if fail:
                with mock.patch.object(self.mod, "_bucket_expr", return_value=(1, "BROKEN SQL")):
                    with self.assertRaises(sqlite3.Error):
                        self.mod.get_power_quality(self.path, 24)
            else:
                self.mod.get_power_quality(self.path, 24)
        return opened

    def test_it_closes_after_success(self):
        opened = self.run_with_tracking(fail=False)
        self.assertTrue(opened and all(c.closed for c in opened))

    def test_it_closes_when_a_query_fails(self):
        opened = self.run_with_tracking(fail=True)
        self.assertTrue(opened and all(c.closed for c in opened))


class VoltageSpreadTests(unittest.TestCase):
    """The spread between phases needs at least two voltages. One voltage alone has no spread."""

    def setUp(self):
        self.mod = fresh("pq_data")

    def spread(self, **volts):
        path = make_db([{"ts": utc_now(), "w": 1, "a1": 1.0, **volts}])
        return self.mod.get_power_quality(path, 24)["summary"]["max_dv"]

    def test_three_phases(self):
        self.assertEqual(self.spread(v1=230.0, v2=236.0, v3=233.0), 6.0)

    def test_two_phases(self):
        self.assertEqual(self.spread(v1=230.0, v2=238.5), 8.5)

    def test_one_phase_has_no_spread(self):
        self.assertIsNone(self.spread(v1=230.0))

    def test_no_voltage_has_no_spread(self):
        self.assertIsNone(self.spread())

    def test_a_spread_at_the_limit_is_counted_and_listed(self):
        path = make_db([{"ts": utc_now(), "w": 1, "v1": 230.0, "v2": 230.0 + self.mod.DV_WARN_V, "v3": 230.0}])
        result = self.mod.get_power_quality(path, 24)
        self.assertEqual(result["aggregates"]["high_dv_cnt"], 1)
        self.assertEqual(len(result["events"]), 1)


AT = "2026-06-15T12:00:00"
AT_DT = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def knotty_rows(hours=48, seed=7):
    """One sample every 10 s, with the cases that are hard: a missing phase, one voltage alone, equal highest values at two
    times, currents and voltages over the limits, voltages with one decimal (so averages land on a rounding edge)."""
    rnd = random.Random(seed)
    rows = []
    t = AT_DT - timedelta(hours=hours) + timedelta(seconds=3)
    while t < AT_DT:
        row = {"ts": t, "w": float(rnd.choice([300, 800, 1500, 9500, 9500, 11000])),
               "v1": round(rnd.uniform(212, 248), 1), "v2": round(rnd.uniform(222, 240), 1),
               "v3": round(rnd.uniform(224, 238), 1),
               "a1": round(rnd.uniform(0, 21), 1), "a2": round(rnd.uniform(0, 9), 1), "a3": round(rnd.uniform(0, 9), 1)}
        roll = rnd.random()
        if roll < 0.03:
            row["v2"] = None
        elif roll < 0.05:
            row["v2"] = row["v3"] = None
        elif roll < 0.06:
            row["v1"] = row["v2"] = row["v3"] = None
        elif roll < 0.07:
            row["w"] = None
        rows.append(row)
        t += timedelta(seconds=10)
    return rows


class MinuteTableTests(unittest.TestCase):
    """The minute table must give the same answer as the samples. compute_from_rows is the reference."""

    def setUp(self):
        self.mod = fresh("pq_data")

    def db(self, rows):
        path = make_db(rows)
        conn = sqlite3.connect(path)
        self.mod.sync_minutes(conn)
        self.addCleanup(conn.close)
        return path, conn

    def assertSame(self, conn, hours, volt_tolerance=0.0):
        """The answers are the same. Only an average voltage in a bucket of 5 minutes or more may differ, by 0.1 V at most:
        the sums are added in another order, so an average that lies exactly between two rounded values can round the other way."""
        rows = self.mod.compute_from_rows(conn, hours, AT)
        minutes = self.mod.compute_from_minutes(conn, hours, AT)
        msg = f"the answers differ for {hours} h"
        self.assertEqual({k: v for k, v in minutes.items() if k != "series"}, {k: v for k, v in rows.items() if k != "series"}, msg)
        self.assertEqual(len(minutes["series"]), len(rows["series"]), msg)
        for got, want in zip(minutes["series"], rows["series"], strict=True):
            for key in want:
                if key in ("v1", "v2", "v3") and volt_tolerance and want[key] is not None and got[key] is not None:
                    self.assertLessEqual(abs(got[key] - want[key]), volt_tolerance + 1e-9, msg)
                else:
                    self.assertEqual(got[key], want[key], f"{msg}, {key} at {want['bucket_utc']}")
        return rows

    def test_every_window_gives_the_same_answer(self):
        _, conn = self.db(knotty_rows())
        for hours in (1, 6, 24):
            with self.subTest(hours=hours):
                result = self.assertSame(conn, hours)
                self.assertGreater(result["aggregates"]["samples_cnt"], 0)

    def test_longer_windows_give_the_same_answer_but_for_the_rounding_of_an_average_voltage(self):
        _, conn = self.db(knotty_rows())
        for hours in (72, 168, 720):
            with self.subTest(hours=hours):
                result = self.assertSame(conn, hours, volt_tolerance=0.1)
                self.assertGreater(result["aggregates"]["samples_cnt"], 0)

    def test_the_knotty_data_really_has_something_in_each_part(self):
        _, conn = self.db(knotty_rows())
        result = self.mod.compute_from_rows(conn, 24, AT)
        self.assertGreater(result["aggregates"]["low_v_cnt"], 0)
        self.assertGreater(result["aggregates"]["high_v_cnt"], 0)
        self.assertGreater(result["aggregates"]["high_a_cnt"], 0)
        self.assertGreater(result["aggregates"]["high_w_cnt"], 0)
        self.assertGreater(result["aggregates"]["high_dv_cnt"], 0)
        self.assertEqual(len(result["events"]), 1000)

    def test_the_thousand_event_limit_cuts_at_the_same_place(self):
        rows = [{"ts": AT_DT - timedelta(seconds=10 * i + 3), "w": 12000.0, "v1": 230.0, "v2": 230.0, "v3": 230.0}
                for i in reversed(range(1500))]
        _, conn = self.db(rows)
        result = self.assertSame(conn, 24)
        self.assertEqual(len(result["events"]), 1000)

    def test_equal_highest_values_give_the_latest_time(self):
        rows = [
            {"ts": AT_DT - timedelta(hours=3), "w": 5000.0, "v1": 230.0, "v2": 238.0, "v3": 230.0},
            {"ts": AT_DT - timedelta(hours=2), "w": 5000.0, "v1": 230.0, "v2": 238.0, "v3": 230.0},
            {"ts": AT_DT - timedelta(hours=1), "w": 100.0, "v1": 230.0, "v2": 231.0, "v3": 230.0},
        ]
        _, conn = self.db(rows)
        result = self.assertSame(conn, 24)
        self.assertEqual(result["summary"]["max_w_ts"], rows[1]["ts"].isoformat())
        self.assertEqual(result["summary"]["max_dv_ts"], rows[1]["ts"].isoformat())

    def test_a_window_with_no_samples_gives_the_same_empty_answer(self):
        _, conn = self.db([{"ts": AT_DT - timedelta(hours=100), "w": 1.0, "v1": 230.0}])
        result = self.assertSame(conn, 24)
        self.assertEqual(result["aggregates"]["samples_cnt"], 0)
        self.assertEqual(result["series"], [])

    def test_voltage_without_a_spread_gives_the_time_of_the_latest_sample_with_a_voltage(self):
        rows = [{"ts": AT_DT - timedelta(hours=2), "w": 1.0, "v1": 230.0},
                {"ts": AT_DT - timedelta(hours=1), "w": 1.0, "v1": 231.0}]
        _, conn = self.db(rows)
        result = self.assertSame(conn, 24)
        self.assertIsNone(result["summary"]["max_dv"])
        self.assertEqual(result["summary"]["max_dv_ts"], rows[1]["ts"].isoformat())

    def test_the_window_starts_at_a_whole_minute(self):
        """The one planned difference: the rows version cuts at the second, the minute table at the minute."""
        row = {"ts": datetime(2026, 6, 15, 11, 0, 10, tzinfo=timezone.utc), "w": 1.0, "v1": 230.0}
        _, conn = self.db([row])
        at = "2026-06-15T12:00:30"
        self.assertEqual(self.mod.compute_from_rows(conn, 1, at)["aggregates"]["samples_cnt"], 0)
        self.assertEqual(self.mod.compute_from_minutes(conn, 1, at)["aggregates"]["samples_cnt"], 1)

    def test_updating_one_minute_at_a_time_gives_the_same_table_as_building_it_at_once(self):
        rows = knotty_rows(hours=2)
        path = make_db()
        step = sqlite3.connect(path)
        for r in rows:
            step.execute("INSERT INTO measurements (ts_utc, active_power_w, active_voltage_l1_v, active_voltage_l2_v,"
                         " active_voltage_l3_v, active_current_l1_a, active_current_l2_a, active_current_l3_a, raw_json)"
                         " VALUES (?,?,?,?,?,?,?,?, '{}')",
                         (r["ts"].isoformat(), r["w"], r["v1"], r["v2"], r["v3"], r["a1"], r["a2"], r["a3"]))
            self.mod.update_minute(step, r["ts"].isoformat())
        one_by_one = step.execute(f"SELECT * FROM {self.mod.MINUTE_TABLE} ORDER BY minute_utc").fetchall()
        step.execute(f"DELETE FROM {self.mod.MINUTE_TABLE}")
        step.commit()
        self.mod.sync_minutes(step)
        at_once = step.execute(f"SELECT * FROM {self.mod.MINUTE_TABLE} ORDER BY minute_utc").fetchall()
        step.close()
        self.assertEqual(one_by_one, at_once)
        self.assertGreater(len(at_once), 100)

    def test_sync_fills_an_empty_table_and_leaves_a_table_in_order_alone(self):
        path = make_db([{"ts": AT_DT - timedelta(minutes=m), "w": 1.0, "v1": 230.0} for m in range(5)])
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(self.mod.sync_minutes(conn), 5)
        self.assertEqual(self.mod.sync_minutes(conn), 0)

    def test_sync_catches_up_with_samples_that_came_without_an_update(self):
        _, conn = self.db([{"ts": AT_DT - timedelta(minutes=10), "w": 1.0, "v1": 230.0}])
        conn.execute("INSERT INTO measurements (ts_utc, raw_json) VALUES (?, '{}')", ((AT_DT - timedelta(minutes=5)).isoformat(),))
        conn.commit()
        self.assertGreater(self.mod.sync_minutes(conn), 0)
        self.assertEqual(conn.execute(f"SELECT SUM(n) FROM {self.mod.MINUTE_TABLE}").fetchone()[0], 2)

    def test_sync_builds_the_table_again_when_the_limits_change(self):
        _, conn = self.db([{"ts": AT_DT - timedelta(minutes=5), "w": 9000.0}])
        self.assertEqual(conn.execute(f"SELECT high_w_n FROM {self.mod.MINUTE_TABLE}").fetchone()[0], 0)
        with mock.patch.object(self.mod, "POWER_PEAK_W", 8000.0):
            self.assertEqual(self.mod.sync_minutes(conn), 1)
            self.assertEqual(conn.execute(f"SELECT high_w_n FROM {self.mod.MINUTE_TABLE}").fetchone()[0], 1)

    def test_minute_rows_older_than_the_oldest_sample_are_kept(self):
        _, conn = self.db([{"ts": AT_DT - timedelta(minutes=5), "w": 1.0}])
        conn.execute(f"INSERT INTO {self.mod.MINUTE_TABLE} (minute_utc, n) VALUES ('2025-01-01T00:00:00', 6)")
        conn.commit()
        self.mod.sync_minutes(conn)
        self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {self.mod.MINUTE_TABLE} WHERE minute_utc < '2026'").fetchone()[0], 1)


class MinuteTableUseTests(unittest.TestCase):
    """When the page reads the minute table, and when it counts from the samples as before."""

    def setUp(self):
        self.mod = fresh("pq_data")

    def recent(self):
        return make_db([{"ts": utc_now() - timedelta(minutes=m), "w": 700.0, "v1": 231.0, "v2": 232.0, "v3": 233.0,
                         "a1": 1.0, "a2": 1.0, "a3": 1.0} for m in range(3)])

    def synced(self):
        path = self.recent()
        conn = sqlite3.connect(path)
        self.mod.sync_minutes(conn)
        conn.close()
        self.mod._CACHE.clear()
        return path

    def test_the_page_reads_the_minute_table_when_it_is_ready(self):
        path = self.synced()
        with mock.patch.object(self.mod, "compute_from_rows", side_effect=AssertionError("read the samples")):
            self.assertEqual(self.mod.get_power_quality(path, 24)["aggregates"]["samples_cnt"], 3)
            self.mod._CACHE.clear()
            self.assertEqual(self.mod.get_power_quality_summary(path, 24)["summary"]["max_v3"], 233.0)

    def test_the_page_counts_from_the_samples_when_the_table_is_not_made(self):
        path = self.recent()
        conn = sqlite3.connect(path)
        conn.execute(f"DROP TABLE {self.mod.MINUTE_TABLE}")
        conn.execute(f"DROP TABLE {self.mod.META_TABLE}")
        conn.commit()
        conn.close()
        with mock.patch.object(self.mod, "compute_from_minutes", side_effect=AssertionError("read the minute table")):
            self.assertEqual(self.mod.get_power_quality(path, 24)["aggregates"]["samples_cnt"], 3)
            self.mod._CACHE.clear()
            self.assertEqual(self.mod.get_power_quality_summary(path, 24)["summary"]["max_v3"], 233.0)

    def test_the_page_counts_from_the_samples_when_the_table_is_behind(self):
        """An old collector adds samples and never the minute rows. The page must not show the old numbers."""
        path = self.synced()
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO measurements (ts_utc, active_voltage_l1_v, raw_json) VALUES (?, 250.0, '{}')",
                     ((utc_now() + timedelta(minutes=2)).isoformat(),))
        conn.commit()
        conn.close()
        self.assertEqual(self.mod.get_power_quality(path, 24)["aggregates"]["samples_cnt"], 4)
        self.mod._CACHE.clear()
        self.assertEqual(self.mod.get_power_quality_summary(path, 24)["summary"]["max_v1"], 250.0)

    def test_the_page_counts_from_the_samples_when_the_limits_are_not_the_ones_the_table_was_made_for(self):
        path = self.synced()
        with mock.patch.object(self.mod, "POWER_PEAK_W", 650.0), \
                mock.patch.object(self.mod, "compute_from_minutes", side_effect=AssertionError("read the minute table")):
            self.assertEqual(self.mod.get_power_quality(path, 24)["aggregates"]["high_w_cnt"], 3)

    def test_an_empty_database_gives_an_empty_answer(self):
        path = make_db()
        result = self.mod.get_power_quality(path, 24)
        self.assertEqual(result["aggregates"]["samples_cnt"], 0)
        self.assertEqual(result["events"], [])


if __name__ == "__main__":
    unittest.main()
