"""The real wheel action feeds source metadata and inherited providers to a plan."""

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--tar", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    metadata = [json.loads(path.read_text()) for path in args.metadata]
    by_name = {item["distribution"]: item for item in metadata}
    assert set(by_name) == {"site-demo", "leaf-package"}, by_name
    wheel_record = by_name["site-demo"]
    assert wheel_record["wheel"]["sha256"] == hashlib.sha256(args.wheel.read_bytes()).hexdigest()
    with zipfile.ZipFile(args.wheel) as wheel:
        names = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        assert len(names) == 1, names
        original = wheel.read(names[0])
        parsed = BytesParser().parsebytes(original)
    assert wheel_record["distribution"] == parsed["Name"]
    assert wheel_record["version"] == parsed["Version"]
    assert wheel_record["wheel"]["metadata_sha256"] == hashlib.sha256(original).hexdigest()
    with tarfile.open(args.tar) as archive:
        installed = {entry.name.removeprefix("./"): entry for entry in archive}
        assert archive.extractfile(installed["opt/runtime/site/" + names[0]]).read() == original
        assert not any("leaf_package" in name for name in installed), "runtime_deps must not be reinstalled"
        assert "opt/runtime/bin/site-demo" in installed
    plan = json.loads(args.plan.read_text())
    assert plan["selected_sources"] == ["leaf-package", "site-demo"], plan
    assert plan["external_requirements"] == ["monitor>=2"], plan
    assert {item["distribution"] for item in plan["sources"]} == set(by_name)


if __name__ == "__main__":
    main()
