#!/usr/bin/env python3
"""
Hermetic tests for where the QBO publisher puts its lock (NO real QBO calls).

  * The lock path is .publish.lock in the config's database_dir.
  * A database_dir that is unset, missing, or not writable is refused with a
    message that names the path and the reason.

publish.py is imported with a scratch config and placeholder credentials. Its
main() never runs.

Run:
    python3 -m unittest scripts.tests.test_qbo_publish_lock_path
"""

import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

SOR_SKIP_REASON = (
    "QBO SDK absent (python-quickbooks); SoR publisher tests skipped. "
    "Install the QBO block from the bookkeeping skill's requirements.txt."
)
try:
    import quickbooks  # noqa: F401
    QBO_SDK_PRESENT = True
except ImportError:
    QBO_SDK_PRESENT = False

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(os.path.dirname(THIS_DIR))
PUBLISH = os.path.join(SKILL_DIR, 'adapters', 'qbo', 'publish.py')
PLACEHOLDERS = {
    'QBO_CLIENT_ID': 'placeholder-id',
    'QBO_CLIENT_SECRET': 'placeholder-secret',
    'QBO_ACCESS_TOKEN': 'placeholder-access',
    'QBO_REFRESH_TOKEN': 'placeholder-refresh',
    'QBO_REALM_ID': '4620816365000000000',
    'QBO_ENVIRONMENT': 'sandbox',
}
CONFIG = """\
local_dir: "{project-root}/_local-bookkeeping"
database_dir: "{local_dir}/database"
database_name: bookkeeping.db
"""


@unittest.skipUnless(QBO_SDK_PRESENT, SOR_SKIP_REASON)
class PublishLockPath(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='publish-lock-path-')
        self.addCleanup(shutil.rmtree, self.root)
        local_dir = os.path.join(self.root, '_local-bookkeeping')
        self.database_dir = os.path.join(local_dir, 'database')
        os.makedirs(self.database_dir)
        config = os.path.join(local_dir, 'config.yaml')
        with open(config, 'w') as f:
            f.write(CONFIG)
        env = dict(PLACEHOLDERS, BOOKKEEPING_CONFIG_PATH=config)
        spec = importlib.util.spec_from_file_location('publish_under_test', PUBLISH)
        self.publish = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, 'path', list(sys.path)):
            spec.loader.exec_module(self.publish)

    def test_lock_path_is_in_the_configured_database_dir(self):
        self.assertEqual(self.publish._config['database_dir'], self.database_dir)
        self.assertEqual(self.publish.publish_lock_path(self.publish._config),
                         os.path.join(self.database_dir, '.publish.lock'))

    def test_lock_path_is_outside_the_skill(self):
        path = self.publish.publish_lock_path(self.publish._config)
        self.assertFalse(os.path.realpath(path).startswith(os.path.realpath(SKILL_DIR) + os.sep))

    def test_unset_database_dir_is_refused(self):
        with self.assertRaises(self.publish.LockPathError) as caught:
            self.publish.publish_lock_path({})
        self.assertEqual(str(caught.exception),
                         'The config has no database_dir. The publish lock is kept there. '
                         'Set database_dir in the config.')

    def test_missing_database_dir_is_refused_with_the_path(self):
        missing = os.path.join(self.root, 'absent')
        with self.assertRaises(self.publish.LockPathError) as caught:
            self.publish.publish_lock_path({'database_dir': missing})
        self.assertIn(missing, str(caught.exception))
        self.assertIn('does not exist', str(caught.exception))

    @unittest.skipIf(os.geteuid() == 0, "root writes through a read-only directory")
    def test_read_only_database_dir_is_refused_with_the_path(self):
        os.chmod(self.database_dir, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.database_dir, stat.S_IRWXU)
        with self.assertRaises(self.publish.LockPathError) as caught:
            self.publish.publish_lock_path({'database_dir': self.database_dir})
        self.assertIn(self.database_dir, str(caught.exception))
        self.assertIn('cannot write', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
