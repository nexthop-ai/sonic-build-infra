"""Exercise the locked BSDtar CLI and the archive consumed by source installers."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile
import unittest

parser = argparse.ArgumentParser()
for name in ("tool", "runtime", "lock", "archive", "payload"):
    parser.add_argument("--" + name, type=Path, required=True)
ARGS, REST = parser.parse_known_args()


def environment():
    result = {k: v for k, v in os.environ.items() if not k.startswith("LD_")}
    result.update(PATH="", LANG="C.UTF-8", LC_ALL="C.UTF-8")
    return result


def run(arguments, *, tool=None, cwd=None):
    return subprocess.run([str((tool or ARGS.tool).absolute()), *arguments], cwd=cwd,
                          env=environment(), capture_output=True, timeout=30)


class BsdtarTest(unittest.TestCase):
    def test_locked_version_and_private_library_closure(self):
        runtime = ARGS.runtime.resolve()
        info = json.loads((runtime / "toolchain.json").read_text())
        architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
        self.assertEqual(info["architecture"], architecture)
        self.assertEqual(info["profile"], "elf")
        self.assertEqual(info["tools"], {"bsdtar": "usr/bin/bsdtar"})
        lock = json.loads(ARGS.lock.read_text())
        selected = lock["dependency_sets"]["bsdtar_tools_debian"]["sets"][architecture]
        key = next(name + "=" + version for name, version in selected.items()
                   if lock["packages"][name + "=" + version]["name"] == "libarchive-tools")
        package = lock["packages"][key]
        expected = package["version"].split(":")[-1].rsplit("-", 1)[0]
        version = run(["--version"])
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn(("bsdtar " + expected).encode(), version.stdout)
        loader = runtime / info["loader"]
        libraries = ":".join(str(runtime / name) for name in info["library_directories"])
        listing = subprocess.run([str(loader), "--library-path", libraries, "--list", str(runtime / info["tools"]["bsdtar"])],
                                 env=environment(), capture_output=True, text=True, timeout=30)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertNotIn("not found", listing.stdout)
        paths = re.findall(r"=>\s+(/\S+)", listing.stdout)
        self.assertTrue(paths)
        # Bazel can materialize a TreeArtifact as per-file symlinks into its
        # producer cache. Compare actual files with the declared tree's members,
        # rather than assuming every resolved path retains the runfiles prefix.
        declared = {path.resolve() for directory in info["library_directories"]
                    for path in (runtime / directory).glob("*.so*") if path.is_file()}
        self.assertTrue(all(Path(path).resolve() in declared for path in paths), listing.stdout)

    def test_original_ownership_paths_modes_and_symlink(self):
        with tarfile.open(ARGS.archive, "r:") as archive:
            members = {str(Path(member.name)): member for member in archive}
            self.assertEqual(set(members), {".", "etc", "etc/path with spaces", "etc/温度.txt", "etc/link"})
            first = members["etc/path with spaces"]
            self.assertEqual((first.uid, first.gid, first.mode, first.mtime), (123, 456, 0o640, 0))
            second = members["etc/温度.txt"]
            self.assertEqual((second.uid, second.gid, second.mode, second.mtime), (0, 0, 0o751, 0))
            for member in (first, second):
                self.assertEqual(archive.extractfile(member).read(), ARGS.payload.read_bytes())
            link = members["etc/link"]
            self.assertTrue(link.issym())
            self.assertEqual(link.linkname, "path with spaces")
            self.assertEqual((link.uid, link.gid, link.mtime), (0, 0, 0))

    def test_hardlinks_relocated_entry_and_missing_input(self):
        with tempfile.TemporaryDirectory(dir=os.environ["TEST_TMPDIR"]) as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            content = b"original hardlink bytes\x00\xff\n"
            (source / "original file").write_bytes(content)
            os.link(source / "original file", source / "hardlink")
            (source / "symlink").symlink_to("original file")
            relocated = root / "entry with spaces"
            relocated.symlink_to(os.path.relpath(ARGS.tool.absolute(), root))
            archive = root / "hardlinks.tar"
            result = run(["--create", "--format=ustar", "--uid=0", "--gid=0", "--file", str(archive),
                          "original file", "hardlink", "symlink"], tool=relocated, cwd=source)
            self.assertEqual(result.returncode, 0, result.stderr)
            with tarfile.open(archive, "r:") as stream:
                members = {entry.name: entry for entry in stream}
                self.assertEqual(set(members), {"original file", "hardlink", "symlink"})
                self.assertTrue(members["hardlink"].islnk())
                self.assertEqual(members["hardlink"].linkname, "original file")
                self.assertEqual(stream.extractfile("original file").read(), content)
                self.assertTrue(all(entry.uid == entry.gid == 0 for entry in members.values()))
            destination = root / "extracted"
            destination.mkdir()
            result = run(["--extract", "--file", str(archive), "--directory", str(destination)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((destination / "hardlink").read_bytes(), content)
            self.assertEqual((destination / "hardlink").stat().st_ino, (destination / "original file").stat().st_ino)
            self.assertEqual(os.readlink(destination / "symlink"), "original file")
            result = run(["--create", "--file", str(root / "missing.tar"), "missing-input"], cwd=source)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(argv=[__file__, *REST])
