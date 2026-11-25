"""Verify different original Debian helper policies in one source package.

The lm-sensors Debian rules select restart-after-upgrade for fancontrol and
no-start for lm-sensors. Real Debhelper output is the observable contract.
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

metadata, tree = map(Path, sys.argv[1:3])
del sys.argv[1:]


class PerPackageControlTest(unittest.TestCase):
    def test_same_source_keeps_each_binary_packages_start_policy(self):
        packages = json.loads(metadata.read_text())["packages"]
        scripts = {}
        for package in ("fancontrol", "lm-sensors"):
            record = packages[package]["control_files"]["postinst"]
            path = tree / record["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
            scripts[package] = path.read_text()
            self.assertEqual(record["mode"], "0755")
        self.assertIn("_dh_action=restart", scripts["fancontrol"])
        self.assertRegex(scripts["fancontrol"], r"invoke-rc.d [^\n]*fancontrol \$_dh_action")
        self.assertNotIn("invoke-rc.d", scripts["lm-sensors"])
        self.assertIn("update-rc.d lm-sensors defaults", scripts["lm-sensors"])

    def test_shared_library_retains_real_ldconfig_activation(self):
        package = json.loads(metadata.read_text())["packages"]["metadata-library"]
        record = package["control_files"]["triggers"]
        contents = (tree / record["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(contents).hexdigest(), record["sha256"])
        active = {line for line in contents.decode().splitlines() if not line.startswith("#")}
        self.assertIn("activate-noawait ldconfig", active)
        self.assertEqual(record["mode"], "0644")


if __name__ == "__main__":
    unittest.main()
