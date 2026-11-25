"""The backup script: it compresses the copy and deletes old copies in both forms."""
import glob
import gzip
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install", "backup-homewizard-db.sh")

# The test host may have no sqlite3 program. This stand-in does the two things the script asks of it.
STUB = """#!/bin/sh
exec python3 -c '
import sqlite3, sys
db, cmd = sys.argv[1], sys.argv[2]
if cmd.startswith(".backup"):
    dst = cmd.split(chr(39))[1]
    s = sqlite3.connect(db); d = sqlite3.connect(dst); s.backup(d); d.close(); s.close()
else:
    c = sqlite3.connect(db); print(c.execute("PRAGMA integrity_check;").fetchone()[0])
' "$@"
"""


class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = os.path.join(self.tmp, "app")
        self.dest = os.path.join(self.tmp, "backups")
        self.bin = os.path.join(self.tmp, "bin")
        for d in (self.app, self.dest, self.bin):
            os.makedirs(d)
        with open(os.path.join(self.app, "collector.py"), "w") as f:
            f.write("print('x')\n")
        self.db = os.path.join(self.app, "homewizard.db")
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY, raw_json TEXT)")
        c.executemany("INSERT INTO measurements (raw_json) VALUES (?)", [('{"a": 1}' * 50,)] * 200)
        c.commit()
        c.close()
        stub = os.path.join(self.bin, "sqlite3")
        with open(stub, "w") as f:
            f.write(STUB)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)

    def run_script(self, retention="14"):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], HW_BASE_DIR=self.app,
                   HW_DB_PATH=self.db, HW_BACKUP_DIR=self.dest, HW_BACKUP_RETENTION_DAYS=retention)
        return subprocess.run(["sh", SCRIPT], env=env, capture_output=True, text=True, check=False)

    def test_copy_is_compressed_and_readable(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(glob.glob(os.path.join(self.dest, "homewizard_*.db")), [])
        files = glob.glob(os.path.join(self.dest, "homewizard_*.db.gz"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(os.stat(files[0]).st_mode), 0o600)
        restored = os.path.join(self.tmp, "restored.db")
        with gzip.open(files[0], "rb") as src, open(restored, "wb") as out:
            shutil.copyfileobj(src, out)
        c = sqlite3.connect(restored)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 200)
        c.close()
        self.assertIn(files[0], r.stdout)

    def test_old_copies_go_in_both_forms_and_new_ones_stay(self):
        old = time.time() - 20 * 86400
        names = ["homewizard_20260101_000000.db", "homewizard_20260101_000001.db.gz",
                 "opt_homewizard_20260101_000000.tar.gz"]
        for n in names:
            path = os.path.join(self.dest, n)
            open(path, "w").close()
            os.utime(path, (old, old))
        recent = os.path.join(self.dest, "homewizard_20261120_000000.db.gz")
        open(recent, "w").close()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        left = sorted(os.listdir(self.dest))
        for n in names:
            self.assertNotIn(n, left)
        self.assertIn("homewizard_20261120_000000.db.gz", left)

    def test_failed_integrity_check_stops_and_deletes_nothing(self):
        stub = os.path.join(self.bin, "sqlite3")
        with open(stub, "w") as f:
            f.write("#!/bin/sh\ncase \"$2\" in .backup*) : > \"$(echo \"$2\" | cut -d\"'\" -f2)\";; *) echo broken;; esac\n")
        old = time.time() - 20 * 86400
        keep = os.path.join(self.dest, "homewizard_20260101_000000.db.gz")
        open(keep, "w").close()
        os.utime(keep, (old, old))
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[BACKUP_ERR]", r.stderr)
        self.assertTrue(os.path.exists(keep))


if __name__ == "__main__":
    unittest.main()
