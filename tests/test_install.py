"""Tests for the install scripts. They run in a temporary folder and never touch /opt or systemd."""
import glob
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from tests.helpers import ROOT

SH = shutil.which("sh")
PYTHON = shutil.which("python3")
MIGRATE = os.path.join(ROOT, "install", "migrate-from-old-layout.sh")
INSTALL = os.path.join(ROOT, "install", "install.sh")

# Made-up values. The number is built here, so the file holds no number of its own.
OLD_METER = "http://192.168.7.7/api/v1/data"
OLD_SIGNAL_URL = "http://192.168.7.8:8080"
OLD_NUMBER = "+4670" + "0000000"


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


@unittest.skipUnless(SH and PYTHON, "needs sh and python3 on the path")
class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hw-install-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = os.path.join(self.tmp, "opt", "homewizard")
        self.backup = os.path.join(self.tmp, "backups")
        self.units = os.path.join(self.tmp, "units")
        for folder in (self.app, self.units):
            os.makedirs(folder)
        # An older install: the settings are written in the files.
        write(os.path.join(self.app, "collector.py"), f'URL = "{OLD_METER}"\nINTERVAL_SEC = 10\n')
        write(os.path.join(self.app, "notify_signal.py"),
              'import os\n'
              f'SIGNAL_URL = os.getenv("HW_SIGNAL_URL", "{OLD_SIGNAL_URL}").rstrip("/")\n'
              f'SIGNAL_FROM = os.getenv("HW_SIGNAL_FROM", "{OLD_NUMBER}")\n'
              f'SIGNAL_TO = os.getenv("HW_SIGNAL_TO", "{OLD_NUMBER}")\n')
        write(os.path.join(self.app, "homewizard.db"), "not a real database")

    def run_script(self, script):
        env = dict(os.environ, HW_BASE_DIR=self.app, HW_BACKUP_DIR=self.backup, HW_UNIT_DIR=self.units)
        return subprocess.run([SH, script], capture_output=True, text=True, env=env)

    def env_lines(self):
        with open(os.path.join(self.app, ".env"), encoding="utf-8") as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if line.startswith("HW_") and "=" in line)

    def test_every_script_has_valid_syntax(self):
        for script in glob.glob(os.path.join(ROOT, "install", "*.sh")):
            result = subprocess.run([SH, "-n", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{os.path.basename(script)}: {result.stderr}")

    def test_the_old_values_move_to_env(self):
        result = self.run_script(MIGRATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.env_lines()
        self.assertEqual(values["HW_METER_URL"], OLD_METER)
        self.assertEqual(values["HW_SIGNAL_URL"], OLD_SIGNAL_URL)
        self.assertEqual(values["HW_SIGNAL_FROM"], OLD_NUMBER)
        self.assertEqual(values["HW_SIGNAL_TO"], OLD_NUMBER)
        self.assertEqual(values["HW_ALARM_PHASE_LIMIT_A"], "20.0")      # the rest of the template stays

    def test_no_number_and_no_address_reach_the_output(self):
        result = self.run_script(MIGRATE)
        shown = result.stdout + result.stderr
        for secret in (OLD_NUMBER, OLD_SIGNAL_URL, OLD_METER):
            self.assertNotIn(secret, shown)

    def test_env_and_archive_are_private(self):
        self.run_script(MIGRATE)
        if os.name != "nt":
            self.assertEqual(os.stat(os.path.join(self.app, ".env")).st_mode & 0o077, 0)
            archive = glob.glob(os.path.join(self.backup, "*.tar.gz"))[0]
            self.assertEqual(os.stat(archive).st_mode & 0o077, 0)

    def test_the_archive_holds_the_program_files_and_not_the_database(self):
        self.run_script(MIGRATE)
        archive = glob.glob(os.path.join(self.backup, "opt_homewizard_before_move_*.tar.gz"))
        self.assertEqual(len(archive), 1)
        with tarfile.open(archive[0]) as tar:
            names = tar.getnames()
        self.assertIn("homewizard/collector.py", names)
        self.assertNotIn("homewizard/homewizard.db", names)

    def test_an_existing_env_is_never_changed(self):
        write(os.path.join(self.app, ".env"), "HW_METER_URL=http://192.168.7.9/api/v1/data\n")
        result = self.run_script(MIGRATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.app, ".env"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "HW_METER_URL=http://192.168.7.9/api/v1/data\n")

    def test_a_value_that_is_not_found_is_listed_and_the_template_line_stays(self):
        write(os.path.join(self.app, "notify_signal.py"), "# no settings here\n")
        result = self.run_script(MIGRATE)
        self.assertIn("Not found, set by hand: HW_SIGNAL_URL, HW_SIGNAL_FROM, HW_SIGNAL_TO", result.stdout)
        self.assertEqual(self.env_lines()["HW_SIGNAL_FROM"], "")

    def test_the_new_layout_is_left_alone(self):
        write(os.path.join(self.app, "config.py"), "# new layout\n")
        result = self.run_script(MIGRATE)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Nothing to move", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.app, ".env")))
        self.assertFalse(os.path.exists(self.backup))

    def test_systemd_variables_are_listed_by_name_only(self):
        write(os.path.join(self.units, "homewizard-api.service"),
              '[Service]\nEnvironment=HW_API_PORT=9999\nEnvironment="HW_POLL_INTERVAL_SEC=30"\n')
        result = self.run_script(MIGRATE)
        self.assertIn("HW_API_PORT HW_POLL_INTERVAL_SEC", result.stdout)
        self.assertNotIn("9999", result.stdout)

    def test_install_refuses_an_older_install_without_env(self):
        result = self.run_script(INSTALL)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("migrate-from-old-layout.sh", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.app, "config.py")), "no file may be copied")


if __name__ == "__main__":
    unittest.main()
