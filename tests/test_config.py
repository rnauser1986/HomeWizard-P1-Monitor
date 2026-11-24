import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, fresh


class DotenvTests(unittest.TestCase):
    def setUp(self):
        self.mod = fresh("config")
        self.dir = tempfile.mkdtemp(prefix="hw-test-")

    def write(self, text):
        path = os.path.join(self.dir, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_values_comments_and_quotes(self):
        path = self.write('# a comment\n\nHW_T_ONE=1\nHW_T_TWO = "two words"\nHW_T_THREE=\'x\'\nno equals sign\n')
        with mock.patch.dict(os.environ, {}):
            self.mod._load_dotenv(path)
            self.assertEqual(os.environ["HW_T_ONE"], "1")
            self.assertEqual(os.environ["HW_T_TWO"], "two words")
            self.assertEqual(os.environ["HW_T_THREE"], "x")
            self.assertNotIn("no equals sign", os.environ)
        self.assertNotIn("HW_T_ONE", os.environ)

    def test_a_variable_that_is_already_set_wins(self):
        path = self.write("HW_T_ONE=from-file\n")
        with mock.patch.dict(os.environ, {"HW_T_ONE": "from-unit"}):
            self.mod._load_dotenv(path)
            self.assertEqual(os.environ["HW_T_ONE"], "from-unit")

    def test_a_missing_file_is_fine(self):
        self.mod._load_dotenv(os.path.join(self.dir, "none.env"))

    def test_a_value_may_hold_an_equals_sign(self):
        path = self.write("HW_T_URL=http://h/x?a=b\n")
        with mock.patch.dict(os.environ, {}):
            self.mod._load_dotenv(path)
            self.assertEqual(os.environ["HW_T_URL"], "http://h/x?a=b")


class DefaultsTests(unittest.TestCase):
    """Run config.py alone in an empty folder, with no HW_ variable: only the defaults are left."""

    def defaults(self, **env):
        folder = tempfile.mkdtemp(prefix="hw-test-")
        with open(os.path.join(ROOT, "config.py"), encoding="utf-8") as src, \
             open(os.path.join(folder, "config.py"), "w", encoding="utf-8") as dst:
            dst.write(src.read())
        clean = {k: v for k, v in os.environ.items() if not k.startswith("HW_")}
        clean.update(env)
        code = ("import config, json; print(json.dumps({k: str(getattr(config, k)) "
                "for k in dir(config) if k.isupper()}))")
        out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); %s" % (folder, code)],
                             capture_output=True, text=True, env=clean, cwd=folder, check=True).stdout
        import json
        return json.loads(out)

    def test_no_installation_value_is_a_default(self):
        d = self.defaults()
        self.assertEqual((d["SIGNAL_URL"], d["SIGNAL_FROM"], d["SIGNAL_TO"]), ("", "", ""))
        self.assertEqual(d["HOMEWIZARD_URL"], d["EXAMPLE_METER_URL"])

    def test_the_defaults_are_the_documented_ones(self):
        d = self.defaults()
        self.assertEqual(d["API_PORT"], "8085")
        self.assertEqual(d["POLL_INTERVAL_SEC"], "10")
        self.assertEqual(d["ALARM_PHASE_LIMIT_A"], "20.0")
        self.assertEqual(d["ALARM_VOLTAGE_LOW_V"], "215.0")
        self.assertEqual(d["PQ_MAX_HOURS"], "168")
        self.assertEqual(d["SIGNAL_COOLDOWN_SEC"], "30.0")

    def test_the_environment_changes_a_value(self):
        self.assertEqual(self.defaults(HW_API_PORT="9090")["API_PORT"], "9090")

    def test_the_hour_limit_stays_between_1_and_720(self):
        self.assertEqual(self.defaults(HW_PQ_MAX_HOURS="99999")["PQ_MAX_HOURS"], "720")
        self.assertEqual(self.defaults(HW_PQ_MAX_HOURS="0")["PQ_MAX_HOURS"], "1")


if __name__ == "__main__":
    unittest.main()
