"""Direct-install contract: explicit payload, target paths, tags and metadata."""
from email.parser import BytesParser
import base64
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from source_package_builder import PackageSource, build_installation


class SourcePackageTest(unittest.TestCase):
    def spec(self, **changes):
        return dict({"distribution": "native-demo", "version": "3.1", "files": [],
                     "site_packages": "/different/site", "scripts": "/different/bin",
                     "headers": "/different/include", "data_prefix": "/different/data",
                     "interpreter": "/different/python3"}, **changes)

    def test_resources_extensions_data_schemes_and_target_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "input"
            original.write_bytes(b"declared payload")
            paths = ["native_demo/_native.so", "native_demo/asset.bin",
                     "native_demo-3.1.data/data/share/config",
                     "native_demo-3.1.data/headers/native.h"]
            spec = self.spec(files=[{"source": str(original), "path": path} for path in paths],
                             abi="cp313", platform="linux_aarch64", python_tag="cp313",
                             entry_points={"console_scripts": ["demo = native_demo:main"]})
            build_installation(spec, root / "tree", root / "mtree", root / "runtime")
            for dest in ("different/site/native_demo/_native.so", "different/site/native_demo/asset.bin",
                         "different/data/share/config", "different/include/native.h"):
                self.assertEqual((root / "tree" / dest).read_bytes(), b"declared payload")
            self.assertTrue((root / "tree/different/bin/demo").read_bytes().startswith(b"#!/different/python3\n"))
            wheel = (root / "tree/different/site/native_demo-3.1.dist-info/WHEEL").read_bytes()
            self.assertEqual(BytesParser().parsebytes(wheel)["Tag"], "cp313-cp313-linux_aarch64")
            runtime = json.loads((root / "runtime").read_text())
            self.assertEqual(runtime["platform"], "linux_aarch64")

    def test_declared_file_modes_and_legacy_executable_payload(self):
        # Installer source exposes executable/non-executable file modes. An
        # explicit declaration must override Bazel-normalized input modes.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "payload"
            original.write_bytes(b"package data")
            for source_mode in (0o444, 0o555):
                original.chmod(source_mode)
                for mode, expected in (("", 0o755 if source_mode == 0o555 else 0o644),
                                       ("0644", 0o644), ("0755", 0o755)):
                    with self.subTest(source_mode=source_mode, mode=mode):
                        destination = root / (str(source_mode) + mode)
                        build_installation(self.spec(
                            files=[{"source": str(original), "path": "native_demo/data"}],
                            regular_file_mode=mode,
                            entry_points={"console_scripts": ["demo = native_demo:main"]}),
                            destination, root / "mtree", root / "runtime")
                        self.assertEqual((destination / "different/site/native_demo/data").stat().st_mode & 0o777, expected)
                        self.assertEqual((destination / "different/bin/demo").stat().st_mode & 0o777, 0o755)
        with self.assertRaises(ValueError):
            PackageSource(self.spec(regular_file_mode="0600"))

    def test_extras_preserve_existing_marker(self):
        source = PackageSource(self.spec(extra_requires={"fast": ["speed>=1; python_version >= '3.12'"]}))
        metadata = BytesParser().parsebytes(source.generated["METADATA"])
        self.assertEqual(metadata["Requires-Dist"], 'speed>=1; (python_version >= \'3.12\') and extra == \'fast\'')

    def test_executable_paths_preserve_scripts_with_regular_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.write_bytes(b"declared content\n")
            payload.chmod(0o555)
            paths = ["native_demo/cli.py", "native_demo-3.1.data/scripts/helper",
                     "native_demo-3.1.data/data/bin/native-helper"]
            spec = self.spec(files=[{"source": str(payload), "path": path} for path in paths],
                             regular_file_mode="0644", executable_files=paths[1:])
            build_installation(spec, root / "tree", root / "mtree", root / "runtime")
            for path, mode in [("different/site/native_demo/cli.py", 0o644),
                               ("different/bin/helper", 0o755),
                               ("different/data/bin/native-helper", 0o755)]:
                self.assertEqual((root / "tree" / path).stat().st_mode & 0o777, mode)
                self.assertEqual((root / "tree" / path).read_bytes(), b"declared content\n")
        for path in ("missing.py", "../escape", "/absolute"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PackageSource(self.spec(executable_files=[path]))

    def test_explicit_script_normalization_uses_target_interpreter_and_updates_record(self):
        # Setuptools/wheel's Python marker is interpreted at install time.
        # The source policy is explicit; modules, shell and no-shebang scripts
        # preserve their bytes, and RECORD must describe the installed content.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {
                "helper": b"#!/usr/bin/env python3\nprint('declared body')\n",
                "ldap.py": b"import ipaddress\n",
                "shell": b"#!/bin/sh\necho declared\n",
            }
            inputs = []
            scripts = []
            for name, contents in payloads.items():
                path = root / name
                path.write_bytes(contents)
                scripts.append("native_demo-3.1.data/scripts/" + name)
                inputs.append({"source": str(path), "path": scripts[-1]})
            module = root / "module.py"
            module.write_bytes(payloads["helper"])
            inputs.append({"source": str(module), "path": "native_demo/module.py"})
            for enabled in (False, True):
                for index, interpreter in enumerate(("/usr/bin/python3", "/target/python3.13")):
                    with self.subTest(normalize_scripts=enabled, interpreter=interpreter):
                        destination = root / (str(enabled) + str(index))
                        build_installation(self.spec(files=inputs, regular_file_mode="0644",
                            executable_files=scripts, normalize_scripts=enabled, interpreter=interpreter),
                            destination, root / "mtree", root / "runtime")
                        record = destination / "different/site/native_demo-3.1.dist-info/RECORD"
                        with record.open(newline="") as source:
                            entries = {row[0]: row[1:] for row in csv.reader(source)}
                        for name, original in payloads.items():
                            expected = (b"#!" + interpreter.encode() + b"\n" + original.split(b"\n", 1)[1]
                                        if enabled and name == "helper" else original)
                            installed = destination / "different/bin" / name
                            self.assertEqual(installed.read_bytes(), expected)
                            self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
                            digest = base64.urlsafe_b64encode(hashlib.sha256(expected).digest()).rstrip(b"=").decode()
                            self.assertEqual(entries["../bin/" + name], ["sha256=" + digest, str(len(expected))])
                        self.assertEqual((destination / "different/site/native_demo/module.py").read_bytes(), payloads["helper"])

    def test_license_file_metadata_version_and_required_payload(self):
        self.assertEqual(BytesParser().parsebytes(PackageSource(self.spec()).generated["METADATA"])["Metadata-Version"], "2.1")
        with tempfile.TemporaryDirectory() as directory:
            license_path = Path(directory) / "LICENSE"
            license_path.write_text("license terms\n")
            source = PackageSource(self.spec(license_files=["LICENSE"],
                extra_distinfo_files=[{"source": str(license_path), "path": "licenses/LICENSE"}]))
            metadata = BytesParser().parsebytes(source.generated["METADATA"])
            self.assertEqual(metadata["Metadata-Version"], "2.4")
            self.assertEqual(metadata.get_all("License-File"), ["LICENSE"])
            self.assertEqual(source.generated["licenses/LICENSE"], b"license terms\n")
        with self.assertRaises(ValueError):
            PackageSource(self.spec(license_files=["LICENSE"]))
        with self.assertRaises(ValueError):
            PackageSource(self.spec(license_files=["../LICENSE"]))

    def test_reject_ambiguous_or_escaping_package_paths(self):
        for path in ("../escape", "/escape", "bad\\path", "demo.dist-info/METADATA", "."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PackageSource(self.spec(files=[{"source": "unused", "path": path}]))
        with self.assertRaises(ValueError):
            PackageSource(self.spec(files=[{"source": "a", "path": "same"}, {"source": "b", "path": "same"}]))

    def test_extra_distinfo_files_preserve_reserved_and_relative_paths(self):
        for path in ("METADATA", "WHEEL", "RECORD", "entry_points.txt", "INSTALLER", "../escape", "/escape"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PackageSource(self.spec(extra_distinfo_files=[{"source": "unused", "path": path}]))

    def test_reject_metadata_header_injection(self):
        with self.assertRaises(ValueError):
            PackageSource(self.spec(requires=["dep>=1\nInjected: true"]))


if __name__ == "__main__":
    unittest.main()
