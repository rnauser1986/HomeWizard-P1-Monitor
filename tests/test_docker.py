"""Tests for the Docker files. They read the files. They do not need Docker."""
import glob
import os
import re
import unittest

from tests.helpers import ROOT


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class DockerFilesTests(unittest.TestCase):
    def test_every_program_file_is_copied_into_the_image(self):
        # pq_route.py needs Flask and is not used by the program, so it stays out.
        copy = " ".join(re.findall(r"^COPY\s+(?:--\S+\s+)?(.*?)\s+\./?$", read("Dockerfile"), re.M | re.S)).replace("\\", " ")
        copied = set(copy.split())
        for path in glob.glob(os.path.join(ROOT, "*.py")):
            name = os.path.basename(path)
            if name == "pq_route.py":
                continue
            self.assertIn(name, copied, f"{name} is not copied by the Dockerfile")
        self.assertIn("ui.html", copied)

    def test_the_image_does_not_run_as_root(self):
        self.assertRegex(read("Dockerfile"), r"(?m)^USER\s+hw\s*$")

    def test_the_database_is_in_the_volume(self):
        self.assertIn("HW_DB_PATH=/data/homewizard.db", read("Dockerfile"))
        compose = read("docker-compose.yml")
        self.assertIn("HW_DB_PATH: /data/homewizard.db", compose)
        self.assertIn("data:/data", compose)

    def test_the_meter_address_is_required(self):
        self.assertIn("${HW_METER_URL:?", read("docker-compose.yml"))

    def test_both_programs_are_services(self):
        compose = read("docker-compose.yml")
        self.assertIn('["python", "collector.py"]', compose)
        self.assertIn('["python", "api_server.py"]', compose)

    def test_the_api_has_a_health_check_on_the_fixed_port(self):
        compose = read("docker-compose.yml")
        self.assertIn("healthcheck:", compose)
        self.assertIn("127.0.0.1:8085/health", compose)
        self.assertIn('HW_API_PORT: "8085"', compose)

    def test_the_secrets_file_stays_out_of_the_image_and_the_compose_file(self):
        ignore = read(".dockerignore").split()
        for name in (".env", "*.db"):
            self.assertIn(name, ignore)
        compose = read("docker-compose.yml")
        self.assertIn("path: .env", compose)
        for name in ("HW_SIGNAL_FROM", "HW_SIGNAL_TO", "HW_SIGNAL_URL"):
            self.assertNotIn(name, compose)

    def test_the_compose_file_does_not_use_a_floating_image_of_someone_else(self):
        self.assertRegex(read("Dockerfile"), r"(?m)^FROM python:3\.\d+-slim\s*$")

    def test_the_containers_are_hardened(self):
        compose = read("docker-compose.yml")
        for text in ("read_only: true", "cap_drop:", "- ALL", "no-new-privileges:true", "max-size:", "max-file:"):
            self.assertIn(text, compose)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", read("Dockerfile"))

    def test_the_systemd_units_are_hardened(self):
        for name in ("homewizard-collector.service", "homewizard-api.service"):
            text = read("install", name)
            for key in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ReadWritePaths=/opt/homewizard", "PrivateTmp=yes", "ProtectHome=yes"):
                self.assertIn(key, text, f"{name}: {key}")

    def test_the_end_to_end_check_tests_the_hardening_and_both_stops(self):
        script = read("tests", "e2e", "run.sh")
        self.assertIn("touch /app/should-not-exist", script)
        self.assertIn("stop -t 10 api", script)
        self.assertIn("stop -t 10 collector", script)


if __name__ == "__main__":
    unittest.main()
