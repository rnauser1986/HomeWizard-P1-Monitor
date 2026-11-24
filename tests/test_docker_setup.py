"""Tests for install/docker-setup.sh. A fake `docker` records its calls, so no container starts."""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT

SH = shutil.which("sh") or "sh"
SCRIPT = os.path.join(ROOT, "install", "docker-setup.sh")
FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
exit 0
"""


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class DockerSetupScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "install"))
        shutil.copy(SCRIPT, os.path.join(self.repo, "install", "docker-setup.sh"))
        with open(os.path.join(self.repo, "docker-compose.yml"), "w") as f:
            f.write("services: {}\n")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.tmp, "docker.log")
        self.add_fake_docker()

    def add_fake_docker(self):
        path = os.path.join(self.bin, "docker")
        with open(path, "w") as f:
            f.write(FAKE_DOCKER)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def run_script(self, args=(), stdin="", env=None, path=None):
        full_env = {"PATH": path or (self.bin + os.pathsep + "/usr/bin:/bin"), "FAKE_DOCKER_LOG": self.log, "HOME": self.tmp}
        full_env.update(env or {})
        return subprocess.run([SH, os.path.join(self.repo, "install", "docker-setup.sh"), "--no-check", *args],
                              input=stdin, capture_output=True, text=True, env=full_env, timeout=30)

    def env_file(self):
        values = {}
        with open(os.path.join(self.repo, ".env")) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    key, value = line.rstrip("\n").split("=", 1)
                    values[key] = value
        return values

    def docker_calls(self):
        with open(self.log) as f:
            return f.read().splitlines()

    def test_a_bare_ip_address_becomes_the_meter_url_and_the_containers_start(self):
        result = self.run_script(stdin="192.168.1.50\n\n\n\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.env_file()
        self.assertEqual(values["HW_METER_URL"], "http://192.168.1.50/api/v1/data")
        self.assertEqual(values["HW_WEB_PORT"], "8085")
        self.assertEqual(values["TZ"], "Europe/Stockholm")
        self.assertEqual(values["HW_PRICE_AREA"], "SE3")
        self.assertEqual(values["HW_SIGNAL_URL"], "")
        # No Dockerfile next to the compose file: a release install, nothing to build.
        self.assertIn("compose up -d ", self.docker_calls()[-1] + " ")
        self.assertNotIn("--build", self.docker_calls()[-1])

    def test_a_checkout_with_a_dockerfile_builds_the_image(self):
        with open(os.path.join(self.repo, "Dockerfile"), "w") as f:
            f.write("FROM scratch\n")
        self.run_script(stdin="192.168.1.50\n\n\n\nn\n")
        self.assertIn("compose up -d --build", self.docker_calls())

    def test_the_env_file_is_private(self):
        self.run_script(stdin="192.168.1.50\n\n\n\nn\n")
        mode = stat.S_IMODE(os.stat(os.path.join(self.repo, ".env")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_full_url_is_kept(self):
        self.run_script(stdin="http://meter.example/api/v1/data\n\n\n\nn\n")
        self.assertEqual(self.env_file()["HW_METER_URL"], "http://meter.example/api/v1/data")

    def test_an_empty_meter_address_is_asked_again(self):
        result = self.run_script(stdin="\n192.168.1.50\n\n\n\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("required", result.stdout)
        self.assertEqual(self.env_file()["HW_METER_URL"], "http://192.168.1.50/api/v1/data")

    def test_a_wrong_port_is_asked_again(self):
        result = self.run_script(stdin="192.168.1.50\nabc\n9090\n\n\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file()["HW_WEB_PORT"], "9090")

    def test_signal_values_are_written_when_the_person_wants_signal(self):
        number = "+" + "1" * 11
        other = "+" + "2" * 11
        stdin = "192.168.1.50\n\n\n\ny\nhttp://signal.example:8080\n%s\n%s\n" % (number, other)
        result = self.run_script(stdin=stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.env_file()
        self.assertEqual(values["HW_SIGNAL_URL"], "http://signal.example:8080")
        self.assertEqual(values["HW_SIGNAL_FROM"], number)
        self.assertEqual(values["HW_SIGNAL_TO"], other)

    def test_an_existing_env_is_kept_when_the_person_says_so(self):
        with open(os.path.join(self.repo, ".env"), "w") as f:
            f.write("HW_METER_URL=http://keep.example/api/v1/data\n")
        result = self.run_script(stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file()["HW_METER_URL"], "http://keep.example/api/v1/data")
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".env.bak")))
        self.assertIn("compose up -d", self.docker_calls()[-1])

    def test_a_replaced_env_is_saved_as_a_backup(self):
        with open(os.path.join(self.repo, ".env"), "w") as f:
            f.write("HW_METER_URL=http://old.example/api/v1/data\n")
        self.run_script(stdin="n\n192.168.1.50\n\n\n\nn\n")
        self.assertEqual(self.env_file()["HW_METER_URL"], "http://192.168.1.50/api/v1/data")
        with open(os.path.join(self.repo, ".env.bak")) as f:
            self.assertIn("old.example", f.read())

    def test_yes_takes_every_answer_from_the_environment(self):
        result = self.run_script(args=["--yes"], env={"HW_METER_URL": "192.168.1.50", "HW_WEB_PORT": "9000"})
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.env_file()
        self.assertEqual(values["HW_METER_URL"], "http://192.168.1.50/api/v1/data")
        self.assertEqual(values["HW_WEB_PORT"], "9000")

    def test_yes_without_a_meter_address_fails(self):
        result = self.run_script(args=["--yes"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HW_METER_URL", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".env")))

    def test_missing_docker_stops_with_a_clear_message(self):
        # A path with only the tools the script needs, and no docker.
        tools = os.path.join(self.tmp, "tools")
        os.makedirs(tools)
        for name in ("dirname", "cp", "chmod", "sed", "tail", "cat"):
            os.symlink(shutil.which(name), os.path.join(tools, name))
        result = self.run_script(stdin="192.168.1.50\n", path=tools)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker is not installed", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".env")))

    def test_an_unknown_option_is_refused(self):
        result = self.run_script(args=["--nope"])
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
