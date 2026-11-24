"""Tests for the files of the Docker end-to-end check. They do not need Docker."""
import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from tests.helpers import ROOT, fresh
from tests.e2e import fake_meter


class FakeMeterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = fake_meter.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_it_answers_the_meter_path_with_json(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/v1/data", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(json.loads(r.read())["active_power_w"], 500)

    def test_any_other_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/other", timeout=5)
        self.assertEqual(raised.exception.code, 404)

    def test_the_collector_can_store_a_reading_from_it(self):
        collector = fresh("collector")
        path = os.path.join(tempfile.mkdtemp(prefix="hw-test-"), "t.db")
        conn = collector.init_db(path)
        try:
            with mock.patch.object(collector, "HOMEWIZARD_URL", f"http://127.0.0.1:{self.port}/api/v1/data"), \
                 mock.patch.object(collector, "check_stale"):
                collector.poll_once(conn)
            row = conn.execute("SELECT active_power_w, total_power_import_kwh FROM measurements").fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(row), (500.0, 100.5))


class E2eFilesTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_script_has_valid_shell_syntax(self):
        result = subprocess.run(["sh", "-n", os.path.join(ROOT, "tests", "e2e", "run.sh")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_script_has_no_windows_line_endings(self):
        with open(os.path.join(ROOT, "tests", "e2e", "run.sh"), "rb") as f:
            self.assertNotIn(b"\r", f.read())

    def test_gitattributes_forces_linux_line_endings(self):
        self.assertIn("* text=auto eol=lf", self.read(".gitattributes"))

    def test_the_override_points_at_the_fake_meter_file(self):
        text = self.read("tests", "e2e", "docker-compose.e2e.yml")
        self.assertIn("./tests/e2e/fake_meter.py:/fake_meter.py", text)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "tests", "e2e", "fake_meter.py")))

    def test_the_workflow_runs_the_check_and_the_linter(self):
        workflow = self.read(".github", "workflows", "build.yml")
        self.assertIn("sh tests/e2e/run.sh", workflow)
        self.assertIn("ruff check", workflow)


if __name__ == "__main__":
    unittest.main()
