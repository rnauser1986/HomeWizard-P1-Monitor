"""Tests for install/get.sh. A fake `curl` serves a local archive and a fake `docker` records its calls."""
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest

from tests.helpers import ROOT

SH = shutil.which("sh") or "sh"
GET = os.path.join(ROOT, "install", "get.sh")
FAKE_DOCKER = '#!/bin/sh\necho "$@" >> "$FAKE_DOCKER_LOG"\nexit 0\n'
# Finds the -o argument, records the URL (the last argument) and copies the archive there.
FAKE_CURL = """#!/bin/sh
out=""; prev=""; url=""
for a in "$@"; do
    [ "$prev" = "-o" ] && out="$a"
    prev="$a"; url="$a"
done
echo "$@" >> "$FAKE_CURL_LOG"
[ -n "${FAKE_CURL_FAIL:-}" ] && exit 22
cp "$FAKE_ARCHIVE" "$out"
"""


def make_executable(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


@unittest.skipUnless(shutil.which("sh") and shutil.which("tar"), "needs a POSIX shell and tar")
class GetScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # An archive with one top folder, like the one from GitHub.
        top = os.path.join(self.tmp, "src", "HomeWizard-P1-Monitor-main")
        os.makedirs(os.path.join(top, "install"))
        shutil.copy(os.path.join(ROOT, "install", "docker-setup.sh"), os.path.join(top, "install", "docker-setup.sh"))
        with open(os.path.join(top, "docker-compose.yml"), "w") as f:
            f.write("services: {}\n")
        self.archive = os.path.join(self.tmp, "main.tar.gz")
        with tarfile.open(self.archive, "w:gz") as tar:
            tar.add(top, arcname="HomeWizard-P1-Monitor-main")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        make_executable(os.path.join(self.bin, "docker"), FAKE_DOCKER)
        make_executable(os.path.join(self.bin, "curl"), FAKE_CURL)
        for name in ("sh", "id", "tar", "gzip", "mktemp", "rm", "dirname", "cp", "chmod", "sed", "tail", "cat", "mkdir"):
            found = next((c for c in ("/usr/bin/" + name, "/bin/" + name) if os.path.exists(c)), shutil.which(name))
            os.symlink(found, os.path.join(self.bin, name))
        self.docker_log = os.path.join(self.tmp, "docker.log")
        self.curl_log = os.path.join(self.tmp, "curl.log")
        self.dir = os.path.join(self.tmp, "install-here")

    def run_script(self, args=(), env=None):
        full = {"PATH": self.bin, "HOME": self.tmp, "HW_INSTALL_DIR": self.dir, "FAKE_ARCHIVE": self.archive,
                "FAKE_DOCKER_LOG": self.docker_log, "FAKE_CURL_LOG": self.curl_log, "HW_METER_URL": "192.168.1.50"}
        full.update(env or {})
        return subprocess.run([SH, GET, *args], capture_output=True, text=True, env=full, stdin=subprocess.DEVNULL, timeout=30)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def test_it_downloads_unpacks_and_starts_the_containers(self):
        result = self.run_script(["--yes", "--no-check"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "install", "docker-setup.sh")))
        self.assertIn("HW_METER_URL=http://192.168.1.50/api/v1/data", self.read(os.path.join(self.dir, ".env")))
        self.assertIn("compose up -d --build", self.read(self.docker_log))
        self.assertIn("https://github.com/rnauser1986/HomeWizard-P1-Monitor/archive/main.tar.gz", self.read(self.curl_log))

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
        if "Downloading" in result.stdout and result.returncode == 0:
            self.skipTest("this machine has a terminal")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--yes", result.stderr)

    def test_a_token_uses_the_api_url_and_the_header(self):
        self.run_script(["--yes", "--no-check"], env={"GITHUB_TOKEN": "token-value"})
        log = self.read(self.curl_log)
        self.assertIn("https://api.github.com/repos/rnauser1986/HomeWizard-P1-Monitor/tarball/main", log)
        self.assertIn("Authorization: Bearer", log)

    def test_a_failed_download_says_so_and_writes_nothing(self):
        result = self.run_script(["--yes", "--no-check"], env={"FAKE_CURL_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("download failed", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".env")))

    def test_missing_docker_stops_before_the_download_when_it_may_not_install_it(self):
        os.remove(os.path.join(self.bin, "docker"))
        result = self.run_script(["--yes", "--no-docker-install"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker is not installed", result.stderr)
        self.assertFalse(os.path.exists(self.curl_log))

    def test_uninstall_removes_the_containers_and_keeps_the_files_and_the_data(self):
        self.run_script(["--yes", "--no-check"])
        result = self.run_script(["--uninstall"])
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.read(self.docker_log).splitlines()
        self.assertIn("compose down", calls)
        self.assertNotIn("compose down -v", calls)
        self.assertTrue(os.path.isdir(self.dir))

    def test_uninstall_with_delete_data_removes_the_volume_and_the_folder(self):
        self.run_script(["--yes", "--no-check"])
        result = self.run_script(["--uninstall", "--delete-data"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("compose down -v", self.read(self.docker_log).splitlines())
        self.assertFalse(os.path.exists(self.dir))

    def test_uninstall_without_an_install_says_so_and_deletes_nothing(self):
        os.makedirs(self.dir)
        result = self.run_script(["--uninstall", "--delete-data"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No install found", result.stderr)
        self.assertTrue(os.path.isdir(self.dir))

    def test_docker_that_does_not_answer_stops_with_a_clear_message(self):
        make_executable(os.path.join(self.bin, "docker"), "#!/bin/sh\nexit 1\n")
        result = self.run_script(["--yes", "--no-docker-install"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker does not answer", result.stderr)


if __name__ == "__main__":
    unittest.main()
