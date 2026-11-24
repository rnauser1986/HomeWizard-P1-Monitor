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


class ReleaseFilesTests(unittest.TestCase):
    def test_the_release_compose_file_is_the_compose_file_without_the_build(self):
        # The release compose file runs the published image. Apart from that it must not differ, or a release install
        # would behave differently from the repository.
        def body(name):
            lines = read(name).splitlines()
            return [ln for ln in lines if not ln.lstrip().startswith("#") and not re.match(r"\s+(build|image):", ln)]
        self.assertEqual(body("docker-compose.yml"), body(os.path.join("install", "compose.release.yml")))

    def test_the_release_compose_file_names_the_published_image(self):
        self.assertIn("image: ghcr.io/rnauser1986/homewizard-p1:${HW_VERSION:-latest}", read("install", "compose.release.yml"))
        self.assertNotIn("build:", read("install", "compose.release.yml"))

    def test_the_image_carries_the_files_that_get_sh_takes_from_it(self):
        dockerfile = read("Dockerfile")
        self.assertIn("install/docker-setup.sh /app/install/docker-setup.sh", dockerfile)
        self.assertIn("install/compose.release.yml /app/install/compose.yml", dockerfile)
        ignore = read(".dockerignore")
        self.assertIn("!install/docker-setup.sh", ignore)
        self.assertIn("!install/compose.release.yml", ignore)

    def test_the_release_workflow_tests_first_and_publishes_an_image_for_the_raspberry_pi(self):
        workflow = read(".github", "workflows", "release.yml")
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("needs: [version, test]", workflow)
        self.assertIn("linux/amd64,linux/arm64", workflow)
        self.assertIn("refs/heads/main", workflow)
        self.assertIn("unittest discover", workflow)
        self.assertIn("tests/e2e/run.sh", workflow)

    def test_the_release_workflow_attaches_get_sh_as_install_sh(self):
        workflow = read(".github", "workflows", "release.yml")
        self.assertIn("install/get.sh > release/install.sh", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("releases/latest/download/install.sh", read("README.md"))

