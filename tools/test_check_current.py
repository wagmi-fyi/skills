#!/usr/bin/env python3
"""
Self-contained tests for scripts/check-current.py, run against every skill's copy.

  * A writable skill directory keeps its last check in .update-check there.
  * A read-only skill directory keeps it under the per-user cache, and a second
    run within a day prints the checked line without a fetch.

The fetch is stubbed. Nothing leaves the machine.

Run, from the repository root:
    uv run --no-project python3 -m unittest tools.test_check_current
"""

import datetime
import importlib.util
import os
import pathlib
import shutil
import stat
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
COPIES = sorted(REPO.glob("*/scripts/check-current.py"))
FRONT_PAGE = """---
name: {name}
metadata:
  version: "abc1234 2026-09-01"
---
"""


def load(path):
    spec = importlib.util.spec_from_file_location("check_current_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckCurrentCache(unittest.TestCase):

    def setUp(self):
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix="check-current-"))
        self.addCleanup(shutil.rmtree, self.scratch)
        self.skill = self.scratch / "skills" / "example-skill"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(FRONT_PAGE.format(name="example-skill"))
        self.cache_home = self.scratch / "cache"
        env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)})
        env.start()
        self.addCleanup(env.stop)
        self.now = datetime.datetime(2026, 9, 17, 9, 0, tzinfo=datetime.timezone.utc)

    def run_twice(self, module):
        fetches = []

        def fetch(url):
            fetches.append(url)
            return "abc1234 2026-09-01"

        with mock.patch.object(module, "fetch_stamp", fetch):
            first = module.check(self.skill, "https://example.invalid", self.now)
            second = module.check(self.skill, "https://example.invalid",
                                  self.now + datetime.timedelta(hours=1))
        return first, second, fetches

    def test_writable_skill_directory_keeps_the_cache_there(self):
        for copy in COPIES:
            with self.subTest(copy=str(copy.relative_to(REPO))):
                (self.skill / ".update-check").unlink(missing_ok=True)
                first, second, fetches = self.run_twice(load(copy))
                self.assertEqual(first, "current")
                self.assertTrue(second.startswith("checked "), second)
                self.assertEqual(len(fetches), 1)
                self.assertTrue((self.skill / ".update-check").is_file())
                self.assertFalse(self.cache_home.exists())

    @unittest.skipIf(os.geteuid() == 0, "root writes through a read-only directory")
    def test_read_only_skill_directory_uses_the_user_cache(self):
        fallback = self.cache_home / "skill-update-check" / "example-skill"
        os.chmod(self.skill, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.skill, stat.S_IRWXU)
        for copy in COPIES:
            with self.subTest(copy=str(copy.relative_to(REPO))):
                fallback.unlink(missing_ok=True)
                first, second, fetches = self.run_twice(load(copy))
                self.assertEqual(first, "current")
                self.assertTrue(second.startswith("checked "), second)
                self.assertEqual(len(fetches), 1)
                self.assertTrue(fallback.is_file())
                self.assertEqual(fallback.read_text().split("\n")[1], "seen abc1234 2026-09-01")
                self.assertFalse((self.skill / ".update-check").exists())

    def test_every_skill_carries_a_copy(self):
        self.assertEqual(len(COPIES), len([p for p in REPO.glob("*/SKILL.md")]))


if __name__ == "__main__":
    unittest.main()
