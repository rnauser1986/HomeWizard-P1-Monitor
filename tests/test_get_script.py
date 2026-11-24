"""Tests for install/get.sh. A fake `docker` serves the files of the image and records its calls. No network is used."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT

SH = shutil.which("sh") or "sh"
GET = os.path.join(ROOT, "install", "get.sh")
# `docker cp reader:/app/install/NAME DEST` copies NAME from the fixture folder.
FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
case "$1" in
    pull) [ -n "${FAKE_PULL_FAIL:-}" ] && exit 1 ;;
    info) [ -n "${FAKE_DOCKER_DOWN:-}" ] && exit 1 ;;
    cp) src="${2#*:}"; cp "$FAKE_IMAGE_FILES/${src##*/}" "$3" ;;
esac
exit 0
"""


def make_executable(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class GetScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # What the image holds under /app/install: the compose file and the setup script.
        self.image_files = os.path.join(self.tmp, "image")
        os.makedirs(self.image_files)
        shutil.copy(os.path.join(ROOT, "install", "compose.release.yml"), os.path.join(self.image_files, "compose.yml"))
        shutil.copy(os.path.join(ROOT, "install", "docker-setup.sh"), self.image_files)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        make_executable(os.path.join(self.bin, "docker"), FAKE_DOCKER)
        self.link_tools()
        self.docker_log = os.path.join(self.tmp, "docker.log")
        self.dir = os.path.join(self.tmp, "install-here")
        self.script = GET

    def link_tools(self):
        for name in ("sh", "id", "mktemp", "rm", "dirname", "cp", "chmod", "sed", "tail", "cat", "mkdir"):
            found = next((c for c in ("/usr/bin/" + name, "/bin/" + name) if os.path.exists(c)), shutil.which(name))
            os.symlink(found, os.path.join(self.bin, name))

    def run_script(self, args=(), env=None):
        full = {"PATH": self.bin, "HOME": self.tmp, "HW_INSTALL_DIR": self.dir, "FAKE_IMAGE_FILES": self.image_files,
                "FAKE_DOCKER_LOG": self.docker_log, "HW_METER_URL": "192.168.1.50"}
        full.update(env or {})
        return subprocess.run([SH, self.script, *args], capture_output=True, text=True, env=full,
                              stdin=subprocess.DEVNULL, timeout=30)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def calls(self):
        return self.read(self.docker_log).splitlines()

    def test_it_pulls_the_image_takes_the_files_from_it_and_starts_the_containers(self):
        result = self.run_script(["--yes", "--no-check"])
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("pull ghcr.io/rnauser1986/homewizard-p1:latest", calls)
        self.assertTrue(any(c.startswith("cp homewizard-p1-install-read:/app/install/compose.yml") for c in calls))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "docker-compose.yml")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "install", "docker-setup.sh")))
        self.assertIn("HW_METER_URL=http://192.168.1.50/api/v1/data", self.read(os.path.join(self.dir, ".env")))
        # A release install has no Dockerfile, so nothing is built.
        self.assertIn("compose up -d", calls)
        self.assertNotIn("compose up -d --build", calls)

    def test_the_reader_container_is_removed_again(self):
        self.run_script(["--yes", "--no-check"])
        calls = self.calls()
        self.assertIn("create --name homewizard-p1-install-read ghcr.io/rnauser1986/homewizard-p1:latest", calls)
        created = calls.index("create --name homewizard-p1-install-read ghcr.io/rnauser1986/homewizard-p1:latest")
        self.assertIn("rm -f homewizard-p1-install-read", calls[created + 1:])

    def test_the_version_of_the_release_is_the_image_tag(self):
        # The Release workflow fills in the RELEASE line with this command.
        self.script = os.path.join(self.tmp, "install.sh")
        with open(GET) as src, open(self.script, "w") as out:
            out.write(re.sub(r'(?m)^RELEASE=""$', 'RELEASE="v2026.11.24"', src.read()))
        result = self.run_script(["--yes", "--no-check"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pull ghcr.io/rnauser1986/homewizard-p1:2026.11.24", self.calls())

    def test_hw_version_chooses_the_image_version(self):
        self.run_script(["--yes", "--no-check"], env={"HW_VERSION": "2026.1.2"})
        self.assertIn("pull ghcr.io/rnauser1986/homewizard-p1:2026.1.2", self.calls())

    def test_the_release_line_can_be_filled_in(self):
        text = self.read(GET)
        self.assertEqual(len(re.findall(r'(?m)^RELEASE=""$', text)), 1)

    def test_a_second_run_keeps_the_env_file(self):
        self.run_script(["--yes", "--no-check"])
        with open(os.path.join(self.dir, ".env"), "a") as f:
            f.write("HW_POLL_INTERVAL_SEC=30\n")
        # --yes without an address in the environment keeps the file as it is.
        result = self.run_script(["--yes", "--no-check"], env={"HW_METER_URL": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HW_POLL_INTERVAL_SEC=30", self.read(os.path.join(self.dir, ".env")))

    def test_with_no_keyboard_and_no_yes_it_stops_with_a_clear_message(self):
        result = self.run_script(["--no-check"])
        if result.returncode == 0:
            self.skipTest("this machine has a terminal")
        self.assertIn("--yes", result.stderr)

    def test_a_failed_download_says_so_and_writes_nothing(self):
        result = self.run_script(["--yes", "--no-check"], env={"FAKE_PULL_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not be downloaded", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".env")))

    def test_missing_docker_stops_when_it_may_not_install_it(self):
        os.remove(os.path.join(self.bin, "docker"))
        result = self.run_script(["--yes", "--no-docker-install"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker is not installed", result.stderr)

    def test_docker_that_does_not_answer_stops_with_a_clear_message(self):
        result = self.run_script(["--yes", "--no-docker-install"], env={"FAKE_DOCKER_DOWN": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker does not answer", result.stderr)

    def test_an_unknown_option_is_refused(self):
        result = self.run_script(["--nope"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)

    def test_the_options_of_get_are_not_passed_on_to_the_setup_script(self):
        # --no-docker-install would be an unknown option for docker-setup.sh.
        result = self.run_script(["--yes", "--no-check", "--no-docker-install"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uninstall_removes_the_containers_and_keeps_the_files_and_the_data(self):
        self.run_script(["--yes", "--no-check"])
        result = self.run_script(["--uninstall"])
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("compose down", calls)
        self.assertNotIn("compose down -v", calls)
        self.assertTrue(os.path.isdir(self.dir))

    def test_uninstall_with_delete_data_removes_the_volume_and_the_folder(self):
        self.run_script(["--yes", "--no-check"])
        result = self.run_script(["--uninstall", "--delete-data"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("compose down -v", self.calls())
        self.assertFalse(os.path.exists(self.dir))

    def test_uninstall_without_an_install_says_so_and_deletes_nothing(self):
        os.makedirs(self.dir)
        result = self.run_script(["--uninstall", "--delete-data"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No install found", result.stderr)
        self.assertTrue(os.path.isdir(self.dir))

    def test_a_download_cut_off_halfway_runs_nothing(self):
        # Every command is inside a function, and the only call is the last line.
        lines = [line for line in self.read(GET).splitlines() if line.strip()]
        self.assertEqual(lines[-1], 'main "$@"')
        top_level = [line for line in lines if not line.startswith((" ", "#", "}", "\t"))]
        for line in top_level:
            self.assertRegex(line, r'^(set -eu|RELEASE=|IMAGE_REPO=|READER=|fail\(\)|main\(\)|uninstall_here\(\)|main "\$@")', line)


if __name__ == "__main__":
    unittest.main()
