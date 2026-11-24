import sqlite3
import unittest
from unittest import mock

from tests.helpers import FakeClock, fresh, make_db, timedelta, utc_now


class AlarmCase(unittest.TestCase):
    """Base class: a fresh alarm module, a fake clock, no real Signal message and a quiet log."""

    module = None

    def setUp(self):
        self.clock = FakeClock()
        self.mod = fresh(self.module)
        for patcher in (mock.patch.object(self.mod.time, "time", self.clock),
                        mock.patch.object(self.mod, "send_signal"),
                        mock.patch("builtins.print")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.send = self.mod.send_signal

    def sent(self):
        return [call.args[0] for call in self.send.call_args_list]


class VoltageAlarmTests(AlarmCase):
    module = "alarm_voltage"

    def test_normal_voltage_sends_nothing(self):
        for _ in range(10):
            self.mod.check_voltage(230.0, 230.0, 230.0)
        self.send.assert_not_called()

    def test_value_on_the_limit_is_normal(self):
        for _ in range(self.mod.NEED + 2):
            self.mod.check_voltage(self.mod.LOW_V, self.mod.HIGH_V, 230.0)
        self.send.assert_not_called()

    def test_low_voltage_needs_consecutive_samples(self):
        for _ in range(self.mod.NEED - 1):
            self.mod.check_voltage(210.0, 230.0, 230.0)
        self.send.assert_not_called()
        self.mod.check_voltage(210.0, 230.0, 230.0)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("L1", self.sent()[0])
        self.assertIn("lowest", self.sent()[0])

    def test_normal_sample_resets_the_counter(self):
        for _ in range(self.mod.NEED - 1):
            self.mod.check_voltage(210.0, 230.0, 230.0)
        self.mod.check_voltage(230.0, 230.0, 230.0)
        self.mod.check_voltage(210.0, 230.0, 230.0)
        self.send.assert_not_called()

    def test_missing_value_never_counts_as_a_hit(self):
        for _ in range(self.mod.NEED + 2):
            self.mod.check_voltage(None, None, None)
        self.send.assert_not_called()

    def test_cooldown_blocks_a_second_alert(self):
        for _ in range(self.mod.NEED):
            self.mod.check_voltage(250.0, 230.0, 230.0)
        self.assertEqual(len(self.sent()), 1)
        for _ in range(self.mod.NEED):
            self.mod.check_voltage(250.0, 230.0, 230.0)
        self.assertEqual(len(self.sent()), 1)
        self.clock.advance(self.mod.COOLDOWN)
        for _ in range(self.mod.NEED):
            self.mod.check_voltage(250.0, 230.0, 230.0)
        self.assertEqual(len(self.sent()), 2)

    def test_low_and_high_are_independent(self):
        for _ in range(self.mod.NEED):
            self.mod.check_voltage(210.0, 250.0, 230.0)
        self.assertEqual(len(self.sent()), 2)

    def test_alert_names_the_extreme_value(self):
        for volts in (214.0, 205.0, 212.0):
            self.mod.check_voltage(volts, 230.0, 230.0)
        self.assertIn("lowest 205.0 V", self.sent()[0])


class PhaseAmpAlarmTests(AlarmCase):
    module = "alarm_phase_amp"

    def test_exact_limit_counts_as_a_hit(self):
        for _ in range(self.mod.NEED):
            self.mod.check_phase_amp(self.mod.LIMIT_A, 1.0, 1.0)
        self.assertEqual(len(self.sent()), 1)

    def test_just_below_limit_sends_nothing(self):
        for _ in range(self.mod.NEED + 3):
            self.mod.check_phase_amp(self.mod.LIMIT_A - 0.1, 1.0, 1.0)
        self.send.assert_not_called()

    def test_each_phase_has_its_own_cooldown(self):
        for _ in range(self.mod.NEED):
            self.mod.check_phase_amp(25.0, 25.0, 1.0)
        self.assertEqual(len(self.sent()), 2)

    def test_missing_value_resets_the_counter(self):
        for _ in range(self.mod.NEED - 1):
            self.mod.check_phase_amp(25.0, 1.0, 1.0)
        self.mod.check_phase_amp(None, 1.0, 1.0)
        self.mod.check_phase_amp(25.0, 1.0, 1.0)
        self.send.assert_not_called()

    def test_message_holds_all_three_phases(self):
        for _ in range(self.mod.NEED):
            self.mod.check_phase_amp(25.0, 3.0, 4.0)
        self.assertIn("L1=25.0 A, L2=3.0 A, L3=4.0 A", self.sent()[0])


class ImbalanceAlarmTests(AlarmCase):
    module = "alarm_imbalance"

    def test_alert_after_need_samples(self):
        for _ in range(self.mod.NEED):
            self.mod.check_imbalance(1.0, 1.0 + self.mod.DIFF_A, 1.0)
        self.assertEqual(len(self.sent()), 1)

    def test_just_below_the_difference_sends_nothing(self):
        for _ in range(self.mod.NEED + 2):
            self.mod.check_imbalance(1.0, 1.0 + self.mod.DIFF_A - 0.1, 1.0)
        self.send.assert_not_called()

    def test_two_phases_are_not_enough_data(self):
        for _ in range(self.mod.NEED + 2):
            self.mod.check_imbalance(1.0, 30.0, None)
        self.send.assert_not_called()


class SpikeAlarmTests(AlarmCase):
    module = "alarm_spike"

    def test_alert_after_need_samples(self):
        for _ in range(self.mod.NEED):
            self.mod.check_spike(self.mod.LIMIT_A, 1.0, 1.0)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("current spike", self.sent()[0])

    def test_below_the_limit_sends_nothing(self):
        for _ in range(self.mod.NEED + 3):
            self.mod.check_spike(self.mod.LIMIT_A - 0.1, 1.0, 1.0)
        self.send.assert_not_called()

    def test_normal_sample_resets_the_counter(self):
        for _ in range(self.mod.NEED - 1):
            self.mod.check_spike(30.0, 1.0, 1.0)
        self.mod.check_spike(1.0, 1.0, 1.0)
        self.mod.check_spike(30.0, 1.0, 1.0)
        self.send.assert_not_called()

    def test_values_as_text_are_read_as_numbers(self):
        for _ in range(self.mod.NEED):
            self.mod.check_spike("23.5", "1.0", "1.0")
        self.assertEqual(len(self.sent()), 1)

    def test_missing_value_skips_the_sample(self):
        for _ in range(self.mod.NEED + 2):
            self.mod.check_spike(30.0, 1.0, None)
        self.send.assert_not_called()

    def test_cooldown_blocks_a_second_alert(self):
        for _ in range(self.mod.NEED * 2):
            self.mod.check_spike(30.0, 1.0, 1.0)
        self.assertEqual(len(self.sent()), 1)
        self.clock.advance(self.mod.COOLDOWN)
        self.mod.check_spike(30.0, 1.0, 1.0)
        self.assertEqual(len(self.sent()), 2)


class StaleAlarmTests(AlarmCase):
    module = "alarm_stale"

    def logged(self):
        import builtins
        return " ".join(str(c.args[0]) for c in builtins.print.call_args_list)

    def old_db(self, extra_seconds=60):
        return make_db([{"ts": utc_now() - timedelta(seconds=self.mod.STALE_SEC + extra_seconds), "w": 100}])

    def test_fresh_data_sends_nothing(self):
        self.mod.check_stale(make_db([{"ts": utc_now(), "w": 100}]))
        self.send.assert_not_called()

    def test_old_data_sends_a_warning(self):
        self.mod.check_stale(self.old_db())
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("no new measurement data", self.sent()[0])
        self.assertIn("[STALE]", self.logged())

    def test_recovery_sends_one_info_message(self):
        path = self.old_db()
        self.mod.check_stale(path)
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO measurements (ts_utc, raw_json) VALUES (?, '{}')", (utc_now().isoformat(),))
        conn.commit()
        conn.close()
        self.mod.check_stale(path)
        self.assertEqual(len(self.sent()), 2)
        self.assertIn("received again", self.sent()[1])
        self.mod.check_stale(path)
        self.assertEqual(len(self.sent()), 2)

    def test_cooldown_blocks_a_second_warning(self):
        path = self.old_db()
        self.mod.check_stale(path)
        self.mod.check_stale(path)
        self.assertEqual(len(self.sent()), 1)
        self.clock.advance(self.mod.COOLDOWN)
        self.mod.check_stale(path)
        self.assertEqual(len(self.sent()), 2)

    def test_time_without_zone_is_read_as_utc(self):
        naive = (utc_now() - timedelta(seconds=self.mod.STALE_SEC + 60)).replace(tzinfo=None)
        self.mod.check_stale(make_db([{"ts": naive, "w": 1}]))
        self.assertEqual(len(self.sent()), 1)

    def test_empty_database_sends_nothing(self):
        self.mod.check_stale(make_db())
        self.send.assert_not_called()

    def test_broken_database_is_logged_and_never_raises(self):
        self.mod.check_stale("/nonexistent-folder/none.db")
        self.assertIn("[STALE_ERR]", self.logged())
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
