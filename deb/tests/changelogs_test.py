"""Original debhelper changelog installation behavior, with real locked tools."""

from pathlib import Path
import sys
import unittest

DEBIAN, UPSTREAM, NEWS, NATIVE, UNTRIMMED, SOURCE, CHANGES = map(Path, sys.argv[1:])
del sys.argv[1:]


class InstalledChangelogs(unittest.TestCase):
    def assert_trimmed_history(self, text):
        # Fixture dates2020..2025 are newer than the locked helper's oldstable
        # cutoff2019-07-06; the two2017/2018 entries are older.
        for year in range(2020, 2026):
            self.assertIn("RECENT_" + str(year), text)
        self.assertNotIn("OLD_2018", text)
        self.assertNotIn("OLD_2017", text)
        self.assertIn("apt changelog changelog-fixture", text)

    def test_non_native_history_upstream_and_news(self):
        self.assert_trimmed_history(DEBIAN.read_text())
        self.assertTrue(DEBIAN.read_text().startswith("changelog-fixture (1.8-1)"))
        self.assertEqual(UPSTREAM.read_bytes(), CHANGES.read_bytes())
        self.assertIn("CURRENT_NEWS", NEWS.read_text())
        self.assertNotIn("OLD_NEWS", NEWS.read_text())

    def test_native_installed_name_and_history(self):
        self.assertEqual(NATIVE.name, "changelog")
        self.assertTrue(NATIVE.read_text().startswith("changelog-fixture (1.8)"))
        self.assert_trimmed_history(NATIVE.read_text())

    def test_original_no_trim_option_preserves_full_history(self):
        self.assertEqual(UNTRIMMED.read_bytes(), SOURCE.read_bytes())


if __name__ == "__main__":
    unittest.main()
