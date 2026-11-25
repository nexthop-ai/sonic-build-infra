"""OCI inheritance keeps parent bytes out of the child while checking closure."""
import argparse
import json
import tarfile
import unittest
from pathlib import Path
from runtime_lock import RuntimeLockError, verify_installation

class InheritanceTest(unittest.TestCase):
    def test_child_payload_keeps_parent_and_external_files_in_parent(self):
        with tarfile.open(ARGS.runtime) as archive:
            names = {member.name.lstrip("./") for member in archive}
        self.assertIn("opt/runtime/site/child.py", names)
        self.assertNotIn("opt/runtime/site/parent.py", names)
        self.assertNotIn("opt/runtime/site/external.py", names)
        self.assertFalse(any("parent-1.0.dist-info/" in name or "external-2.0.dist-info/" in name for name in names))

    def test_changed_parent_and_unplanned_package_are_rejected(self):
        plan = json.loads(Path(ARGS.plan).read_text())
        lock = Path(ARGS.lock).read_text()
        verify_installation(plan, lock, [ARGS.child, ARGS.parent], [ARGS.external])
        with self.assertRaisesRegex(RuntimeLockError, "metadata differs from producer"):
            verify_installation(plan, lock, [ARGS.child, ARGS.wrong], [ARGS.external])
        with self.assertRaisesRegex(RuntimeLockError, "source installation set differs"):
            verify_installation(plan, lock, [ARGS.child, ARGS.parent, ARGS.unexpected], [ARGS.external])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["runtime", "plan", "lock", "child", "parent", "wrong", "external", "unexpected"]:
        parser.add_argument("--" + name, required=True)
    ARGS, remaining = parser.parse_known_args()
    unittest.main(argv=[__file__] + remaining)
