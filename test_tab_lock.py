import tempfile
import unittest
from pathlib import Path

from tab_lock import TabLockUnavailable, tab_state_lock


class TabLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "tab.lock"

    def test_lock_file_is_created(self):
        with tab_state_lock(self.path):
            self.assertTrue(self.path.exists())

    def test_second_lock_is_rejected(self):
        with tab_state_lock(self.path):
            with self.assertRaises(TabLockUnavailable):
                with tab_state_lock(self.path):
                    self.fail("second lock unexpectedly acquired")

    def test_lock_is_reusable_after_release(self):
        with tab_state_lock(self.path):
            pass
        with tab_state_lock(self.path):
            self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
