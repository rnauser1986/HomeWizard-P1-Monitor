import json
import os
import sqlite3
import unittest
from unittest import mock

from tests.helpers import fresh, make_db


def reply(items):
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = json.dumps(items).encode()
    return r


class TariffTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("tariff_mod")
        self.path = make_db()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def set_config(self, **values):
        base = {"id": 1, "start_date": "2026-11-01", "mode": "fixed", "interval_min": 60, "area": "SE3",
                "fixed_sek": 1.5, "add_sek": 0.0, "vat_pct": 0.0}
        base.update(values)
        self.conn.execute("INSERT OR REPLACE INTO tariff_config VALUES (:id,:start_date,:mode,:interval_min,:area,:fixed_sek,:add_sek,:vat_pct)", base)
        self.conn.commit()

    def add_spot(self, slot, sek):
        self.conn.execute("INSERT OR REPLACE INTO spot_prices VALUES (?, ?)", (slot, sek))
        self.conn.commit()

    def rate(self, when):
        return self.mod.effective_rate(self.conn.cursor(), self.path, when)


class ApplyTariffTests(TariffTestCase):
    def test_surcharge_then_vat(self):
        self.assertEqual(self.mod.apply_tariff(1.50, {"add_sek": 0.08, "vat_pct": 25.0}), round((1.50 + 0.08) * 1.25, 4))

    def test_missing_values_count_as_zero(self):
        self.assertEqual(self.mod.apply_tariff(1.5, {"add_sek": None, "vat_pct": None}), 1.5)


class DefaultConfigTests(TariffTestCase):
    def test_an_empty_table_gives_the_defaults_from_the_settings(self):
        cfg = self.mod.get_cfg(self.conn.cursor())
        self.assertEqual(cfg["mode"], "fixed")
        self.assertEqual(cfg["fixed_sek"], self.mod.DEFAULT_FIXED_SEK)
        self.assertEqual(cfg["area"], self.mod.DEFAULT_PRICE_AREA)
        self.assertEqual(cfg["start_date"], self.mod.DEFAULT_TARIFF_START_DATE)


class FixedPriceTests(TariffTestCase):
    def test_fixed_mode_uses_the_fixed_price(self):
        self.set_config(mode="fixed", fixed_sek=2.0, vat_pct=25.0)
        self.assertEqual(self.rate("2026-12-01 12:00"), 2.5)

    def test_before_the_start_date_the_price_is_fixed_even_in_dynamic_mode(self):
        self.set_config(mode="dynamic", fixed_sek=1.0)
        self.add_spot("2026-10-15 12:00", 9.0)
        self.assertEqual(self.rate("2026-10-15 12:30"), 1.0)


class DynamicPriceTests(TariffTestCase):
    def setUp(self):
        super().setUp()
        self.set_config(mode="dynamic", fixed_sek=1.0)
        patcher = mock.patch.object(self.mod, "sync_spots")
        self.sync = patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_price_of_the_hour_is_used(self):
        self.add_spot("2026-11-24 12:00", 0.80)
        self.assertEqual(self.rate("2026-11-24 12:59"), 0.80)
        self.sync.assert_not_called()

    def test_quarter_hour_slots_when_the_interval_is_15(self):
        self.set_config(mode="dynamic", interval_min=15, fixed_sek=1.0)
        self.add_spot("2026-11-24 12:00", 0.80)
        self.add_spot("2026-11-24 12:15", 0.95)
        self.assertEqual(self.rate("2026-11-24 12:29"), 0.95)

    def test_a_missing_price_triggers_a_sync_and_then_the_fixed_price(self):
        self.assertEqual(self.rate("2026-11-24 12:00"), 1.0)
        self.sync.assert_called_once()

    def test_a_price_that_the_sync_brings_is_used(self):
        self.sync.side_effect = lambda *a, **k: self.add_spot("2026-11-24 12:00", 0.55)
        self.assertEqual(self.rate("2026-11-24 12:10"), 0.55)

    def test_a_price_older_than_the_limit_is_not_used(self):
        self.add_spot("2026-11-22 20:00", 9.0)           # 40 hours before the slot
        self.assertEqual(self.rate("2026-11-24 12:00"), 1.0)
        self.sync.assert_called_once()

    def test_a_price_just_inside_the_limit_is_used(self):
        self.add_spot("2026-11-23 00:00", 0.70)          # exactly 36 hours before
        self.assertEqual(self.rate("2026-11-24 12:00"), 0.70)

    def test_surcharge_and_vat_apply_to_the_spot_price(self):
        self.set_config(mode="dynamic", add_sek=0.10, vat_pct=25.0)
        self.add_spot("2026-11-24 12:00", 0.50)
        self.assertEqual(self.rate("2026-11-24 12:00"), round((0.50 + 0.10) * 1.25, 4))


class SyncTests(TariffTestCase):
    def test_the_price_service_is_called_at_most_once_in_the_cooldown(self):
        clock = [1000.0]
        with mock.patch.object(self.mod, "fetch_day_spots") as fetch, \
             mock.patch.object(self.mod.time, "time", lambda: clock[0]):
            self.mod.sync_spots(self.path, "SE3")
            self.assertEqual(fetch.call_count, 2)           # today and tomorrow
            self.mod.sync_spots(self.path, "SE3")
            self.assertEqual(fetch.call_count, 2)
            clock[0] += self.mod.SYNC_COOLDOWN_SEC
            self.mod.sync_spots(self.path, "SE3")
            self.assertEqual(fetch.call_count, 4)

    def test_a_forced_sync_ignores_the_cooldown(self):
        with mock.patch.object(self.mod, "fetch_day_spots") as fetch:
            self.mod.sync_spots(self.path, "SE3")
            self.mod.sync_spots(self.path, "SE3", force=True)
            self.assertEqual(fetch.call_count, 4)


class FetchTests(TariffTestCase):
    def stored(self):
        return dict(sqlite3.connect(self.path).execute("SELECT slot_key, sek_per_kwh FROM spot_prices").fetchall())

    def test_prices_are_stored_by_slot(self):
        items = [{"time_start": "2026-11-24T12:00:00+01:00", "SEK_per_kWh": 0.5},
                 {"time_start": "2026-11-24T13:00:00+01:00", "SEK_per_kWh": "0.75"}]
        with mock.patch("urllib.request.urlopen", return_value=reply(items)) as urlopen:
            self.mod.fetch_day_spots(self.path, "SE3", "2026-11-24")
        self.assertIn("2026/11-24_SE3.json", urlopen.call_args.args[0])
        self.assertEqual(self.stored(), {"2026-11-24 12:00": 0.5, "2026-11-24 13:00": 0.75})

    def test_bad_items_are_skipped(self):
        items = [{"time_start": "2026-11-24T12:00:00+01:00", "SEK_per_kWh": "abc"},
                 {"time_start": "2026-11-24T13:00:00+01:00"},
                 {"SEK_per_kWh": 1.0},
                 {"time_start": "2026-11-24T14:00:00+01:00", "SEK_per_kWh": 2.0}]
        with mock.patch("urllib.request.urlopen", return_value=reply(items)):
            self.mod.fetch_day_spots(self.path, "SE3", "2026-11-24")
        self.assertEqual(self.stored(), {"2026-11-24 14:00": 2.0})

    def test_an_answer_that_is_not_a_list_changes_nothing(self):
        with mock.patch("urllib.request.urlopen", return_value=reply({"error": "x"})):
            self.mod.fetch_day_spots(self.path, "SE3", "2026-11-24")
        self.assertEqual(self.stored(), {})

    def test_a_failing_service_never_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.mod.fetch_day_spots(self.path, "SE3", "2026-11-24")
        self.assertEqual(self.stored(), {})


if __name__ == "__main__":
    unittest.main()
