"""Check conflicting installation destinations are rejected before packaging."""

import unittest

from debug_symbols_manifest import manifest


class ManifestTest(unittest.TestCase):
    def test_conflicting_build_ids_are_rejected(self):
        spec = "\n".join(
            f"{repo}/.build-id/ab/same.debug type=file uid=0 gid=0 mode=0755 time=17 content={repo}/symbols"
            for repo in ("first", "second")
        )
        with self.assertRaisesRegex(ValueError, "duplicate debug archive path"):
            manifest(spec, "", {})

    def test_installation_link_cannot_replace_symbol(self):
        spec = "symbols/.build-id/ab/file.debug type=file time=17 content=original"
        with self.assertRaisesRegex(ValueError, "duplicate debug archive path"):
            manifest(spec, "", {"usr/lib/debug/.build-id/ab/file.debug": "elsewhere"})

    def test_installation_link_stays_relative(self):
        for name in ("/usr/link", "usr/../link"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid installation symlink"):
                manifest("", "", {name: "target"})


if __name__ == "__main__":
    unittest.main()
