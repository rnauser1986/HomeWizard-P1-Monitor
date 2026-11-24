"""Tests for the rules in AGENTS.md: English only, no secrets, standard library only, one settings list."""
import glob
import os
import re
import sys
import unittest

from tests.helpers import ROOT


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def code_files():
    return sorted(glob.glob(os.path.join(ROOT, "*.py"))) + [os.path.join(ROOT, "ui.html")]


def all_text_files(skip=()):
    files = code_files() + glob.glob(os.path.join(ROOT, "tests", "*.py"))
    files += glob.glob(os.path.join(ROOT, "install", "*")) + glob.glob(os.path.join(ROOT, "docs", "*.md"))
    files += [os.path.join(ROOT, n) for n in (".env.example", "README.md", "AGENTS.md")]
    return [f for f in files if os.path.isfile(f) and os.path.basename(f) not in skip]


class RepoRuleTests(unittest.TestCase):
    def test_everything_is_in_english(self):
        # This file holds the letters in its own pattern, so it is not scanned.
        for path in all_text_files(skip=("test_repo_rules.py",)):
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    self.assertIsNone(re.search("[åäöÅÄÖ]", line), f"{os.path.basename(path)}:{number}: {line.strip()[:80]}")

    def test_no_swedish_word_is_left(self):
        # Words without å, ä or ö. The letter check above cannot find them.
        words = re.compile(r"\b(effekt|vecka|veckor|ingen|inga|idag|kostnad|skala|statistik|varning|avvikelser?|toppar|fasbalans|fasobalans)\b", re.I)
        for path in all_text_files(skip=("test_repo_rules.py",)):
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    found = words.search(line)
                    self.assertIsNone(found, f"{os.path.basename(path)}:{number}: {found and found.group(0)}")

    def test_no_installation_value_is_in_the_repository(self):
        phone = re.compile(r"\+\d{7,}")
        private_ip = re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        for path in all_text_files():
            text = slurp(path)
            self.assertIsNone(phone.search(text), f"phone number in {os.path.basename(path)}")
            self.assertIsNone(private_ip.search(text), f"private address in {os.path.basename(path)}")

    def test_only_the_standard_library_is_imported(self):
        local = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(ROOT, "*.py"))}
        allowed_extra = {"pq_route.py": {"flask"}}     # a known gap, see docs/ARCHITECTURE.md
        for path in sorted(glob.glob(os.path.join(ROOT, "*.py"))):
            name = os.path.basename(path)
            for module in re.findall(r"^(?:from|import)\s+([A-Za-z_][\w]*)", slurp(path), re.M):
                ok = module in sys.stdlib_module_names or module in local or module in allowed_extra.get(name, set())
                self.assertTrue(ok, f"{name} imports {module}")

    def test_env_example_and_config_list_the_same_settings(self):
        in_config = set(re.findall(r'getenv\("(HW_[A-Z0-9_]+)"', read("config.py")))
        in_example = set(re.findall(r"^#? ?(HW_[A-Z0-9_]+)=", read(".env.example"), re.M))
        self.assertEqual(in_config, in_example)

    def test_architecture_lists_every_setting(self):
        text = read("docs/ARCHITECTURE.md")
        for name in re.findall(r'getenv\("(HW_[A-Z0-9_]+)"', read("config.py")):
            self.assertTrue(f"`{name}`" in text, f"{name} is not in docs/ARCHITECTURE.md")

    def test_the_env_file_is_ignored_by_git(self):
        ignored = read(".gitignore").split()
        self.assertIn(".env", ignored)
        self.assertIn("*.db", ignored)

    def test_no_module_reads_the_environment_outside_config(self):
        for path in code_files():
            if os.path.basename(path) in ("config.py", "ui.html"):
                continue
            self.assertNotIn("os.getenv", slurp(path), os.path.basename(path))
            self.assertNotIn("os.environ", slurp(path), os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
