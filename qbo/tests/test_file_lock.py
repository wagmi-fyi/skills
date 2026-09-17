#!/usr/bin/env python3
"""
Self-contained tests for FileLock in qbo_client.

  * A free lock is taken, and release removes the file.
  * A held lock returns False.
  * A lock file that cannot be created raises OSError with its errno and path.

The client is imported with placeholder credentials in the environment, so it
reads no settings file and asks no token service.

Run, from the qbo skill directory:
    uv run --with-requirements requirements.txt python3 -m unittest tests.test_file_lock
"""

import errno
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(THIS_DIR), "scripts")
PLACEHOLDERS = {
    "QBO_CLIENT_ID": "placeholder-id",
    "QBO_CLIENT_SECRET": "placeholder-secret",
    "QBO_ACCESS_TOKEN": "placeholder-access",
    "QBO_REFRESH_TOKEN": "placeholder-refresh",
    "QBO_REALM_ID": "4620816365000000000",
    "QBO_ENVIRONMENT": "sandbox",
}

try:
    import dotenv  # noqa: F401
    DOTENV_PRESENT = True
except ImportError:
    DOTENV_PRESENT = False


def _file_lock():
    with mock.patch.dict(os.environ, PLACEHOLDERS), \
            mock.patch.object(sys, "path", [SCRIPTS] + sys.path):
        import qbo_client
    return qbo_client.FileLock


@unittest.skipUnless(DOTENV_PRESENT, "python-dotenv is not installed; run under the skill's requirements.txt")
class FileLockTests(unittest.TestCase):

    def setUp(self):
        self.FileLock = _file_lock()
        self.scratch = tempfile.mkdtemp(prefix="qbo-file-lock-")
        self.addCleanup(shutil.rmtree, self.scratch)
        self.path = os.path.join(self.scratch, ".publish.lock")

    def test_free_lock_is_taken_and_release_removes_the_file(self):
        lock = self.FileLock(self.path)
        self.assertTrue(lock.acquire())
        with open(self.path) as f:
            self.assertEqual(f.read(), str(os.getpid()))
        lock.release()
        self.assertFalse(os.path.exists(self.path))

    def test_held_lock_returns_false(self):
        holder = self.FileLock(self.path)
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        self.assertFalse(self.FileLock(self.path).acquire())
        with open(self.path) as f:
            self.assertEqual(f.read(), str(os.getpid()))

    @unittest.skipIf(os.geteuid() == 0, "root writes through a read-only directory")
    def test_read_only_directory_raises_with_the_path(self):
        os.chmod(self.scratch, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.scratch, stat.S_IRWXU)
        with self.assertRaises(OSError) as caught:
            self.FileLock(self.path).acquire()
        self.assertEqual(caught.exception.errno, errno.EACCES)
        self.assertEqual(caught.exception.filename, self.path)


if __name__ == "__main__":
    unittest.main()
