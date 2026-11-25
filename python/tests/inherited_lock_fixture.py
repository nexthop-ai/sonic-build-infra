"""Fixed, resolver-free test input record for the inherited-image fixture."""
import hashlib
import json
import sys
from pathlib import Path

plan, requirements, lock, inputs = map(Path, sys.argv[1:])
requirements.write_text("# Generated from source Requires-Dist for this target environment.\nexternal==2.0\n")
lock.write_text("external==2.0 --hash=sha256:" + hashlib.sha256(b"external fixture distribution").hexdigest() + "\n")
identity = json.dumps(json.loads(plan.read_text()), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
inputs.write_text(json.dumps({"schema_version": 1, "plan_sha256": hashlib.sha256(identity).hexdigest(),
    "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
    "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()}))
