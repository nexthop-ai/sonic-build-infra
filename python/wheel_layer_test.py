import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from wheel_layer_builder import build_layer
import runtime_lock


class WheelLayerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wheel(self, additions=None, name="sample", version="1.0"):
        stem = name + "-" + version
        files = {
            name + "/__init__.py": b"value = 42\n",
            name + "/data.json": b'{"example": true}\n',
            stem + ".dist-info/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
            stem + ".dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            stem + ".dist-info/entry_points.txt": f"[console_scripts]\n{name} = {name}.cli:main\n".encode(),
            stem + ".data/scripts/" + ("helper" if name == "sample" else name + "_helper"): b"#!python\nprint('helper')\n",
            stem + ".data/data/share/" + name + "/example.txt": b"shared data\n",
        }
        files.update(additions or {})
        records = io.StringIO()
        writer = csv.writer(records, lineterminator="\n")
        for name, content in sorted(files.items()):
            hash_ = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
            writer.writerow([name, "sha256=" + hash_, len(content)])
        writer.writerow([stem + ".dist-info/RECORD", "", ""])
        files[stem + ".dist-info/RECORD"] = records.getvalue().encode()
        path = self.root / (stem + "-py3-none-any.whl")
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o100755 if name.endswith("/helper") else 0o100644) << 16
                archive.writestr(info, content)
        return path

    def layer(self, wheels, output="layer.tar"):
        destination = self.root / output
        build_layer(wheels, destination, "/usr/local/lib/python3.13/dist-packages",
                    "/usr/local/bin", "/usr/local/include", "/usr/local", "/usr/bin/python3")
        return destination

    def test_layout_metadata_data_and_entry_points(self):
        output = self.layer([self.wheel()])
        with tarfile.open(output) as archive:
            members = {member.name: member for member in archive.getmembers()}
            site = "./usr/local/lib/python3.13/dist-packages/"
            self.assertEqual(archive.extractfile(site + "sample/data.json").read(), b'{"example": true}\n')
            self.assertEqual(archive.extractfile("./usr/local/share/sample/example.txt").read(), b"shared data\n")
            entrypoint = archive.extractfile("./usr/local/bin/sample").read()
            self.assertTrue(entrypoint.startswith(b"#!/usr/bin/python3\n"))
            self.assertIn(b"from sample.cli import main", entrypoint)
            self.assertTrue(archive.extractfile("./usr/local/bin/helper").read().startswith(b"#!/usr/bin/python3\n"))
            self.assertEqual(members["./usr/local/bin/sample"].mode, 0o755)
            self.assertEqual(members["./usr/local/bin/helper"].mode, 0o755)
            self.assertEqual(members[site + "sample/data.json"].mode, 0o644)
            record = archive.extractfile(site + "sample-1.0.dist-info/RECORD").read()
            self.assertIn(b"../../../bin/sample,sha256=", record)
            self.assertIn(site + "sample-1.0.dist-info/INSTALLER", members)
            for member in members.values():
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertNotIn("__pycache__", member.name)

    def test_reproducible_output(self):
        wheel = self.wheel()
        first = self.layer([wheel], "first.tar").read_bytes()
        second = self.layer([wheel], "second.tar").read_bytes()
        self.assertEqual(first, second)

    def test_conflicting_wheels_fail(self):
        wheel = self.wheel()
        with self.assertRaises(FileExistsError):
            self.layer([wheel, wheel])

    def test_invalid_record_is_rejected(self):
        wheel = self.wheel()
        with zipfile.ZipFile(wheel) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files["sample/data.json"] = b"changed"
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        with self.assertRaises(ValueError):
            self.layer([wheel])
        with self.assertRaises(ValueError):
            self.installed_with_metadata([wheel])
        self.assertFalse((self.root / "source0.json").exists())

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.layer([self.wheel({"../escape": b"no"})])

    def test_entrypoint_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.layer([self.wheel({"sample-1.0.dist-info/entry_points.txt": b"[console_scripts]\n../../escape = sample:main\n"})])

    def installed_with_metadata(self, wheels, prefix="source"):
        tar = self.root / (prefix + ".tar")
        outputs = [self.root / (prefix + str(index) + ".json") for index in range(len(wheels))]
        build_layer(wheels, tar, "/usr/local/lib/python3.13/dist-packages",
                    "/usr/local/bin", "/usr/local/include", "/usr/local", "/usr/bin/python3",
                    metadata_outputs=outputs)
        return tar, outputs

    def plan(self, metadata, roots):
        return runtime_lock.make_plan({
            "roots": roots, "python_version": "3.13", "python_platform": "x86_64-unknown-linux-gnu",
            "uv_version": "0.12.18", "index_urls": [], "find_links": [], "no_index": True,
            "marker_environment": {}, "source_distributions": roots,
            "site_packages": ["/usr/local/lib/python3.13/dist-packages"], "constraints": [],
            "metadata": [{"path": str(path), "producer": "//:owner_wheels"} for path in metadata],
        })

    def test_metadata_and_tar_come_from_identical_validated_wheel(self):
        metadata = (b"Metadata-Version: 2.1\nName: sample\nVersion: 1.0\nRequires-Python: >=3.11\n"
                    b"Requires-Dist: leaf>=2; python_version >= '3.12'\n"
                    b"Requires-Dist: metrics>=4; extra == 'metrics'\nProvides-Extra: metrics\n\n")
        wheel = self.wheel({"sample-1.0.dist-info/METADATA": metadata})
        archive, outputs = self.installed_with_metadata([wheel])
        record = json.loads(outputs[0].read_text())
        self.assertEqual(record["distribution"], "sample")
        self.assertEqual(record["version"], "1.0")
        self.assertEqual(record["requires_python"], ">=3.11")
        self.assertEqual(record["extras"], ["metrics"])
        self.assertEqual(record["wheel"], {
            "filename": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "metadata_sha256": hashlib.sha256(metadata).hexdigest(), "tags": ["py3-none-any"],
        })
        plan = self.plan(outputs, ["sample[metrics]"])
        self.assertEqual(plan["external_requirements"], ["leaf>=2", "metrics>=4"])
        with tarfile.open(archive) as installed:
            self.assertEqual(installed.extractfile(
                "./usr/local/lib/python3.13/dist-packages/sample-1.0.dist-info/METADATA").read(), metadata)
        self.assertEqual(archive.read_bytes(), self.layer([wheel], "legacy.tar").read_bytes())

    def test_multiple_source_wheels_and_locked_external_classification(self):
        wheel = self.wheel({"sample-1.0.dist-info/METADATA":
                            b"Metadata-Version: 2.1\nName: sample\nVersion: 1.0\nRequires-Dist: common>=1\n"})
        common = self.wheel({"common-1.0.dist-info/METADATA":
                             b"Metadata-Version: 2.1\nName: common\nVersion: 1.0\nRequires-Dist: external>=2\n"},
                            name="common")
        source_tar, outputs = self.installed_with_metadata([wheel, common])
        plan = self.plan(outputs, ["sample"])
        self.assertEqual(plan["selected_sources"], ["common", "sample"])
        self.assertEqual(plan["external_requirements"], ["external>=2"])
        external_tar, _ = self.installed_with_metadata([self.wheel(name="external", version="2.0")], "external")
        lock = "external==2.0 --hash=sha256:" + "a" * 64 + "\n"
        runtime_lock.verify_installation(plan, lock, [source_tar], [external_tar])
        with self.assertRaisesRegex(runtime_lock.RuntimeLockError, "source installation set differs"):
            runtime_lock.verify_installation(plan, lock, [external_tar], [source_tar])
        with self.assertRaisesRegex(runtime_lock.RuntimeLockError, "third-party installation set differs"):
            runtime_lock.verify_installation(plan, lock, [source_tar], [])

    def test_rules_python_patched_filename_preserves_installed_metadata_and_identity(self):
        for version, archive_version in (("1.0", "1.0+patched"),
                                         ("1.0+vendor.1", "1.0+vendor.1.patched")):
            with self.subTest(version=version):
                original = self.wheel(version=version)
                wheel = original.rename(self.root / f"sample-{archive_version}-py3-none-any.whl")
                archive, outputs = self.installed_with_metadata([wheel])
                record = json.loads(outputs[0].read_text())
                self.assertEqual(record["version"], version)
                self.assertEqual(record["wheel"]["filename"], wheel.name)
                self.assertEqual(record["wheel"]["sha256"], hashlib.sha256(wheel.read_bytes()).hexdigest())
                with zipfile.ZipFile(wheel) as source, tarfile.open(archive) as installed:
                    path = f"sample-{version}.dist-info/METADATA"
                    self.assertEqual(installed.extractfile(
                        "./usr/local/lib/python3.13/dist-packages/" + path).read(), source.read(path))
                self.assertEqual(archive.read_bytes(), self.layer([wheel], "legacy.tar").read_bytes())

    def test_patch_suffix_does_not_hide_other_identity_or_tag_mismatches(self):
        cases = (
            ("1.0", "sample-2.0+patched-py3-none-any.whl"),
            ("1.0", "sample-1.0+vendor-py3-none-any.whl"),
            ("1.0", "sample-1.0+patched.extra-py3-none-any.whl"),
            ("1.0+vendor.1", "sample-1.0+vendor.2.patched-py3-none-any.whl"),
            ("1.0", "different-1.0+patched-py3-none-any.whl"),
            ("1.0", "sample-1.0+patched-cp311-cp311-linux_aarch64.whl"),
        )
        for version, filename in cases:
            with self.subTest(filename=filename):
                wheel = self.wheel(version=version).rename(self.root / filename)
                with self.assertRaises(ValueError):
                    self.installed_with_metadata([wheel])
                self.assertFalse((self.root / "source0.json").exists())

    def test_patched_wheel_still_requires_matching_record_hashes(self):
        wheel = self.wheel().rename(self.root / "sample-1.0+patched-py3-none-any.whl")
        with zipfile.ZipFile(wheel) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files["sample/data.json"] = b"changed after patching"
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        with self.assertRaises(ValueError):
            self.installed_with_metadata([wheel])
        self.assertFalse((self.root / "source0.json").exists())

    def test_stale_or_substituted_installed_metadata_fails(self):
        _, metadata = self.installed_with_metadata([self.wheel()])
        plan = self.plan(metadata, ["sample"])
        changed = self.wheel({"sample-1.0.dist-info/METADATA":
                              b"Metadata-Version: 2.1\nName: sample\nVersion: 1.0\nRequires-Dist: new-dependency\n"})
        changed_tar, changed_metadata = self.installed_with_metadata([changed], "changed")
        changed_plan = self.plan(changed_metadata, ["sample"])
        self.assertNotEqual(runtime_lock.digest(runtime_lock.canonical_json(plan)),
                            runtime_lock.digest(runtime_lock.canonical_json(changed_plan)))
        requirements = self.root / "requirements.in"
        lock = self.root / "requirements_lock.txt"
        inputs = self.root / "lock-inputs.json"
        requirements.write_text(runtime_lock.requirements_text(plan))
        lock.write_text("")
        inputs.write_text(json.dumps({
            "schema_version": 1,
            "plan_sha256": runtime_lock.digest(runtime_lock.canonical_json(plan)),
            "requirements_sha256": runtime_lock.digest(requirements.read_bytes()),
            "lock_sha256": runtime_lock.digest(lock.read_bytes()),
        }))
        runtime_lock.check(plan, requirements, lock, inputs)
        with self.assertRaisesRegex(runtime_lock.RuntimeLockError, "runtime lock is stale"):
            runtime_lock.check(changed_plan, requirements, lock, inputs)
        with self.assertRaisesRegex(runtime_lock.RuntimeLockError, "metadata differs from producer"):
            runtime_lock.verify_installation(plan, "", [changed_tar], [])

    def test_missing_or_mismatched_metadata_rejected_before_publication(self):
        for metadata in (
            b"Name: sample\nVersion: 1.0\n",
            b"Metadata-Version: 2.1\nVersion: 1.0\n",
            b"Metadata-Version: 2.1\nName: sample\nName: another\nVersion: 1.0\n",
            b"Metadata-Version: 2.1\nName: different\nVersion: 1.0\n",
            b"Metadata-Version: 2.1\nName: sample\nVersion: 7.0\n",
            b"Metadata-Version: 2.1\nName: sample\nVersion: 1.0\nRequires-Dist: <>invalid\n",
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.installed_with_metadata([self.wheel({"sample-1.0.dist-info/METADATA": metadata})])
            self.assertFalse((self.root / "source0.json").exists())

    def test_same_source_wheel_identity_and_metadata_are_reproducible(self):
        wheel = self.wheel()
        first, first_metadata = self.installed_with_metadata([wheel], "first")
        second, second_metadata = self.installed_with_metadata([wheel], "second")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first_metadata[0].read_bytes(), second_metadata[0].read_bytes())

    def test_wheel_tag_mismatch_is_rejected(self):
        wheel = self.wheel({"sample-1.0.dist-info/WHEEL":
                            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp311-cp311-linux_aarch64\n"})
        with self.assertRaisesRegex(ValueError, "tags disagree"):
            self.installed_with_metadata([wheel])

    def test_metadata_output_count_must_match_wheels(self):
        with self.assertRaisesRegex(ValueError, "one metadata output"):
            build_layer([self.wheel()], self.root / "bad.tar", "/site", "/bin", "/include", "/data", "/python",
                        metadata_outputs=[])


if __name__ == "__main__":
    unittest.main()
