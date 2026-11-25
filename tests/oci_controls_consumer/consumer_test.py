"""Consume real finalized Debian fields without a source-helper dependency."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import unittest

archive_path, receipt_path, metadata_path, helper_archive, helper_metadata = map(Path, sys.argv[1:])
sys.argv = sys.argv[:1]


class ConsumerTest(unittest.TestCase):
    def test_original_package_identity_and_control_bytes(self):
        with tarfile.open(archive_path) as archive:
            control = archive.getmember('control')
            data = archive.extractfile(control).read()
            self.assertEqual((control.uid, control.gid, control.mode, control.mtime), (0, 0, 0o644, 0))
            self.assertEqual({item.name for item in archive}, {'control'})
        fields = dict(line.split(': ', 1) for line in data.decode().splitlines() if ': ' in line)
        self.assertEqual(fields['Package'], 'control-consumer')
        self.assertEqual(fields['Version'], '1.0')
        self.assertEqual(fields['Architecture'], 'all')
        self.assertEqual(fields['Description'], 'Declared source-control projection consumer')
        package = json.loads(metadata_path.read_text())['packages']['control-consumer']
        self.assertEqual(package['control_files']['control']['sha256'], hashlib.sha256(data).hexdigest())
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['package'], package['control_fields'])
        self.assertEqual(receipt['payload_sha256'], package['payload_sha256'])


    def test_generated_service_scripts_are_projected_with_their_modes(self):
        controls = json.loads(helper_metadata.read_text())['packages']['control-consumer']['control_files']
        with tarfile.open(helper_archive) as archive:
            self.assertEqual({item.name for item in archive}, set(controls))
            for name, record in controls.items():
                item = archive.getmember(name)
                self.assertEqual(hashlib.sha256(archive.extractfile(item).read()).hexdigest(), record['sha256'])
                self.assertEqual((item.uid, item.gid, item.mode, item.mtime), (0, 0, int(record['mode'], 8), 0))
            postinst = archive.extractfile('postinst').read().decode()
            self.assertEqual(archive.getmember('postinst').mode, 0o755)
            self.assertIn('update-rc.d control-consumer defaults', postinst)
            self.assertNotIn('invoke-rc.d', postinst)


if __name__ == '__main__':
    unittest.main()
