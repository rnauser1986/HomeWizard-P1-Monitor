import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests.helpers import fresh

METER_ANSWER = {
    "wifi_strength": 80,
    "active_power_w": 1234.0,
    "active_power_l1_w": 400.0, "active_power_l2_w": 400.0, "active_power_l3_w": 434.0,
    "active_voltage_l1_v": 231.0, "active_voltage_l2_v": 232.0, "active_voltage_l3_v": 233.0,
    "active_current_a": 5.5,
    "active_current_l1_a": 1.5, "active_current_l2_a": 2.5, "active_current_l3_a": 3.5,
    "total_power_import_kwh": 1000.5, "total_power_export_kwh": 10.25,
}


def meter_reply(data):
    reply = mock.MagicMock()
    reply.__enter__.return_value.read.return_value = json.dumps(data).encode()
    return reply


class InitDbTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("collector")
        self.path = os.path.join(tempfile.mkdtemp(prefix="hw-test-"), "t.db")

    def tables(self):
        conn = sqlite3.connect(self.path)
        try:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()

    def test_all_four_tables_are_created(self):
        self.mod.init_db(self.path).close()
        self.assertTrue({"measurements", "historical_kwh", "tariff_config", "spot_prices"} <= self.tables())

    def test_the_database_uses_wal_mode(self):
        conn = self.mod.init_db(self.path)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            conn.close()

    def test_a_second_start_keeps_the_data(self):
        conn = self.mod.init_db(self.path)
        conn.execute("INSERT INTO measurements (ts_utc, raw_json) VALUES ('2026-01-01T00:00:00+00:00', '{}')")
        conn.execute("INSERT INTO historical_kwh VALUES ('day', '2025-12-31', 4.2)")
        conn.commit()
        conn.close()
        self.mod.init_db(self.path).close()
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT kwh FROM historical_kwh").fetchone()[0], 4.2)
        finally:
            conn.close()


class PollOnceTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("collector")
        self.path = os.path.join(tempfile.mkdtemp(prefix="hw-test-"), "t.db")
        self.conn = self.mod.init_db(self.path)
        self.addCleanup(self.conn.close)
        self.alarms = {}
        for name in ("check_spike", "check_imbalance", "check_phase_amp", "check_voltage", "check_stale"):
            patcher = mock.patch.object(self.mod, name)
            self.alarms[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def poll(self, data=METER_ANSWER):
        with mock.patch("urllib.request.urlopen", return_value=meter_reply(data)) as urlopen:
            self.mod.poll_once(self.conn)
        return urlopen

    def row(self):
        self.conn.row_factory = sqlite3.Row
        return self.conn.execute("SELECT * FROM measurements").fetchone()

    def test_the_reading_is_stored(self):
        self.poll()
        row = self.row()
        self.assertEqual(row["active_power_w"], 1234.0)
        self.assertEqual(row["active_voltage_l2_v"], 232.0)
        self.assertEqual(row["active_current_l3_a"], 3.5)
        self.assertEqual(row["total_power_import_kwh"], 1000.5)
        self.assertEqual(json.loads(row["raw_json"]), METER_ANSWER)
        self.assertTrue(row["ts_utc"].endswith("+00:00"))

    def test_the_meter_is_read_with_the_timeout_from_the_settings(self):
        urlopen = self.poll()
        self.assertEqual(urlopen.call_args.args[0], self.mod.HOMEWIZARD_URL)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], self.mod.METER_TIMEOUT_SEC)

    def test_every_alarm_gets_its_values(self):
        self.poll()
        self.alarms["check_spike"].assert_called_once_with(1.5, 2.5, 3.5)
        self.alarms["check_imbalance"].assert_called_once_with(1.5, 2.5, 3.5)
        self.alarms["check_phase_amp"].assert_called_once_with(1.5, 2.5, 3.5)
        self.alarms["check_voltage"].assert_called_once_with(231.0, 232.0, 233.0)
        self.alarms["check_stale"].assert_called_once_with(self.mod.DB_PATH)

    def test_fields_the_meter_leaves_out_are_stored_as_null(self):
        self.poll({"active_power_w": 500.0})
        row = self.row()
        self.assertEqual(row["active_power_w"], 500.0)
        self.assertIsNone(row["active_voltage_l1_v"])
        self.alarms["check_voltage"].assert_called_once_with(None, None, None)

    def test_a_meter_error_stores_nothing(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("timed out")):
            with self.assertRaises(OSError):
                self.mod.poll_once(self.conn)
        self.assertIsNone(self.row())


class MainLoopTests(unittest.TestCase):
    class StopLoop(Exception):
        pass

    def setUp(self):
        self.mod = fresh("collector")
        patchers = {
            "init_db": mock.patch.object(self.mod, "init_db"),
            "warn": mock.patch.object(self.mod, "warn_if_not_set_up"),
            "check_stale": mock.patch.object(self.mod, "check_stale"),
            "print": mock.patch("builtins.print"),
        }
        self.m = {name: p.start() for name, p in patchers.items()}
        for p in patchers.values():
            self.addCleanup(p.stop)

    def run_loop(self, poll_side_effect, passes):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= passes:
                raise self.StopLoop()

        with mock.patch.object(self.mod, "poll_once", side_effect=poll_side_effect), \
             mock.patch.object(self.mod.time, "sleep", sleep):
            with self.assertRaises(self.StopLoop):
                self.mod.main()
        return sleeps

    def test_a_failed_poll_never_stops_the_loop(self):
        sleeps = self.run_loop([OSError("timed out"), None, OSError("again")], passes=3)
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(self.m["check_stale"].call_count, 2)

    def test_a_failed_poll_is_logged_with_its_tag(self):
        self.run_loop([OSError("timed out")], passes=1)
        logged = " ".join(str(c.args[0]) for c in self.m["print"].call_args_list)
        self.assertIn("[POLL_ERR] timed out", logged)

    def test_the_loop_sleeps_for_the_poll_interval(self):
        sleeps = self.run_loop([None], passes=1)
        self.assertEqual(sleeps, [self.mod.POLL_INTERVAL_SEC])


class ShutdownTests(unittest.TestCase):
    """The collector closes its database connection when it stops."""

    def setUp(self):
        self.mod = fresh("collector")

    def run_main(self, sleep_error):
        conn = mock.MagicMock()
        with mock.patch.object(self.mod, "init_db", return_value=conn), \
             mock.patch.object(self.mod, "warn_if_not_set_up"), \
             mock.patch.object(self.mod, "poll_once"), \
             mock.patch.object(self.mod.time, "sleep", side_effect=sleep_error):
            with self.assertRaises((KeyboardInterrupt, SystemExit, MemoryError)):
                self.mod.main()
        return conn

    def test_it_closes_on_ctrl_c(self):
        self.run_main(KeyboardInterrupt()).close.assert_called_once()

    def test_it_closes_on_a_normal_exit(self):
        self.run_main(SystemExit(0)).close.assert_called_once()

    def test_it_closes_on_an_unexpected_error(self):
        self.run_main(MemoryError()).close.assert_called_once()

    def test_the_stop_handler_is_a_normal_exit(self):
        with self.assertRaises(SystemExit) as raised:
            self.mod._stop(signal.SIGTERM, None)
        self.assertEqual(raised.exception.code, 0)

    def test_init_db_closes_the_connection_when_the_setup_fails(self):
        connection = mock.MagicMock()
        with mock.patch.object(self.mod.sqlite3, "connect", return_value=connection), \
             mock.patch.object(self.mod, "_create_schema", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                self.mod.init_db("x.db")
        connection.close.assert_called_once()

    @unittest.skipIf(os.name == "nt", "SIGTERM works differently on Windows")
    def test_the_real_program_stops_cleanly_on_sigterm(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, "HW_DB_PATH": os.path.join(tempfile.mkdtemp(prefix="hw-test-"), "t.db"),
               "HW_METER_URL": "http://127.0.0.1:9/api/v1/data", "HW_POLL_INTERVAL_SEC": "1"}
        proc = subprocess.Popen([sys.executable, "collector.py"], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            time.sleep(1.5)
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


class StartLineTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("collector")

    def test_the_start_line_says_whether_signal_is_on_and_shows_no_value(self):
        secret_url, secret_from, secret_to = "http://signal.invalid:8080", "+" + "1" * 11, "+" + "2" * 11   # built at run time, so no number is in the file
        with mock.patch.object(self.mod, "SIGNAL_URL", secret_url), \
             mock.patch.object(self.mod, "SIGNAL_FROM", secret_from), \
             mock.patch.object(self.mod, "SIGNAL_TO", secret_to):
            line = self.mod.describe_start()
        self.assertIn("Signal on", line)
        for value in (secret_url, secret_from, secret_to, self.mod.HOMEWIZARD_URL):
            self.assertNotIn(value, line)

    def test_signal_off_is_said(self):
        with mock.patch.object(self.mod, "SIGNAL_URL", ""):
            self.assertIn("Signal off", self.mod.describe_start())

    def test_the_loop_prints_the_start_line_first(self):
        with mock.patch.object(self.mod, "init_db", return_value=mock.MagicMock()), \
             mock.patch.object(self.mod, "warn_if_not_set_up"), \
             mock.patch.object(self.mod, "poll_once"), \
             mock.patch.object(self.mod.time, "sleep", side_effect=KeyboardInterrupt()), \
             mock.patch("builtins.print") as printed:
            with self.assertRaises(KeyboardInterrupt):
                self.mod.main()
        self.assertTrue(str(printed.call_args_list[0].args[0]).startswith("[START] collector"))


class StartWarningTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("collector")
        self.printed = mock.patch("builtins.print").start()
        self.addCleanup(mock.patch.stopall)

    def run_warning(self, url, signal):
        with mock.patch.object(self.mod, "HOMEWIZARD_URL", url), \
             mock.patch.object(self.mod, "SIGNAL_URL", signal), \
             mock.patch.object(self.mod, "SIGNAL_FROM", signal), \
             mock.patch.object(self.mod, "SIGNAL_TO", signal):
            self.mod.warn_if_not_set_up()
        return " ".join(str(c.args[0]) for c in self.printed.call_args_list)

    def test_example_settings_are_said_loudly(self):
        text = self.run_warning(self.mod.EXAMPLE_METER_URL, "")
        self.assertIn("[CONFIG] Signal is not set up", text)
        self.assertIn("[CONFIG] HW_METER_URL is still the example address", text)

    def test_real_settings_are_quiet(self):
        self.assertEqual(self.run_warning("http://meter.test/api/v1/data", "x"), "")


if __name__ == "__main__":
    unittest.main()
