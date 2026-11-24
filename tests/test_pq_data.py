import unittest

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


if __name__ == "__main__":
    unittest.main()
