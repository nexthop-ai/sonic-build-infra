"""Check configurable installation paths preserve declared payloads and symbols."""

from pathlib import Path
import sys
import tarfile
import unittest


def members(path):
    with tarfile.open(path) as archive:
        return {
            item.name.removeprefix("./"): (
                item.type, item.mode, item.uid, item.gid, item.mtime,
                item.linkname,
                archive.extractfile(item).read() if item.isfile() else None,
            )
            for item in archive
        }


class ConfiguredInstallPathsTest(unittest.TestCase):
    def test_paths_payloads_and_metadata(self):
        runtime, symbols, stripped, reference_symbols, source, prefix = sys.argv[1:]
        actual = members(runtime)
        self.assertEqual(
            {name for name, item in actual.items() if item[0] != tarfile.DIRTYPE},
            {prefix + "/hello", prefix + "/data.txt", "usr/bin/hello"},
        )
        self.assertEqual(actual[prefix + "/hello"], (
            tarfile.REGTYPE, 0o755, 0, 0, 17, "", Path(stripped).read_bytes(),
        ))
        self.assertEqual(actual[prefix + "/data.txt"], (
            tarfile.REGTYPE, 0o644, 0, 0, 19, "", Path(source).read_bytes(),
        ))
        self.assertEqual(actual["usr/bin/hello"], (
            tarfile.SYMTYPE, 0o777, 0, 0, 23, "../../" + prefix + "/hello", None,
        ))
        self.assertEqual(members(symbols), members(reference_symbols))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
