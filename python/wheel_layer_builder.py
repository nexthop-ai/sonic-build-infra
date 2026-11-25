#!/usr/bin/env python3
"""Install wheels with PyPA installer and create an owned, deterministic tar."""

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
import zipfile

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.sources import WheelFile
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version


def absolute_directory(path):
    parsed = PurePosixPath(path)
    if not parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
        raise ValueError(f"Expected an absolute target path without traversal: {path}")
    return str(parsed)


def validate_paths(wheel):
    with zipfile.ZipFile(wheel) as archive:
        seen = set()
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                raise ValueError(f"Invalid wheel member path: {entry.filename}")
            if path in seen:
                raise ValueError(f"Duplicate wheel member path: {entry.filename}")
            seen.add(path)


class LayerDestination(SchemeDictionaryDestination):
    def write_to_fs(self, scheme, path, stream, is_executable):
        # Entry-point names also produce files but do not occur in the ZIP member
        # list. Validate every destination path, including those generated names.
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in path:
            raise ValueError(f"Invalid wheel installation path: {path}")
        return super().write_to_fs(scheme, path, stream, is_executable)


def _single_header(message, name, required=False):
    values = message.get_all(name, [])
    if len(values) > 1 or (required and (not values or not values[0].strip())):
        raise ValueError(f"Wheel metadata requires one {name} header")
    return values[0] if values else ""


def wheel_runtime_metadata(wheel, source):
    """Read requirements from the same validated wheel passed to the installer."""
    raw = source.read_dist_info("METADATA").encode("utf-8")
    message = BytesParser().parsebytes(raw)
    if message.defects:
        raise ValueError(f"Malformed wheel METADATA: {message.defects}")
    _single_header(message, "Metadata-Version", required=True)
    name = canonicalize_name(_single_header(message, "Name", required=True), validate=True)
    version = Version(_single_header(message, "Version", required=True))
    filename_name, filename_version, _, filename_tags = parse_wheel_filename(Path(wheel).name)
    # rules_python's patch_whl.bzl appends exactly one local "patched" segment
    # to the archive filename while retaining the installed METADATA version.
    # Keep that precise upstream convention separate from arbitrary mismatches.
    patched_version = Version(str(version) + (".patched" if version.local else "+patched"))
    if name != filename_name or filename_version not in (version, patched_version):
        raise ValueError(
            f"Wheel filename and METADATA distribution/version disagree: {Path(wheel).name} "
            f"declares {name}=={version}")
    wheel_message = BytesParser().parsebytes(source.read_dist_info("WHEEL").encode("utf-8"))
    tags = {tag for value in wheel_message.get_all("Tag", []) for tag in parse_tag(value)}
    if tags != filename_tags:
        raise ValueError("Wheel filename and WHEEL tags disagree")
    return {
        "distribution": name,
        "version": str(version),
        "requires_python": str(SpecifierSet(_single_header(message, "Requires-Python"))),
        "requires_dist": sorted(str(Requirement(value)) for value in message.get_all("Requires-Dist", [])),
        "extras": sorted(canonicalize_name(value, validate=True) for value in message.get_all("Provides-Extra", [])),
        "wheel": {
            "filename": Path(wheel).name,
            "sha256": hashlib.sha256(Path(wheel).read_bytes()).hexdigest(),
            "metadata_sha256": hashlib.sha256(raw).hexdigest(),
            "tags": sorted(str(tag) for tag in tags),
        },
    }


def build_layer(wheels, output, site_packages, scripts, headers, data, interpreter, metadata_outputs=None):
    if metadata_outputs is not None and len(metadata_outputs) != len(wheels):
        raise ValueError("Expected one metadata output for each selected wheel")
    scheme = {
        "purelib": absolute_directory(site_packages),
        "platlib": absolute_directory(site_packages),
        "scripts": absolute_directory(scripts),
        "headers": absolute_directory(headers),
        "data": absolute_directory(data),
    }
    absolute_directory(interpreter)
    # installer creates normal files/directories using the process umask. Fix it
    # for reproducibility; executable wheel entries and launchers stay executable.
    previous_umask = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory() as directory:
            destination = LayerDestination(
                scheme_dict=scheme,
                interpreter=interpreter,
                script_kind="posix",
                bytecode_optimization_levels=(),
                destdir=directory,
            )
            metadata = []
            for wheel in wheels:
                validate_paths(wheel)
                with WheelFile.open(wheel) as source:
                    source.validate_record(validate_contents=True)
                    if metadata_outputs is not None:
                        metadata.append(wheel_runtime_metadata(wheel, source))
                    install(source, destination, additional_metadata={"INSTALLER": b"sonic-build-infra\n"})
            root = Path(directory)
            with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(root.rglob("*")):
                    member = archive.gettarinfo(str(path), arcname="./" + path.relative_to(root).as_posix())
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    member.mtime = 0
                    if member.isfile():
                        with path.open("rb") as contents:
                            archive.addfile(member, contents)
                    else:
                        archive.addfile(member)
            for path, record in zip(metadata_outputs or [], metadata):
                Path(path).write_text(json.dumps(record, sort_keys=True) + "\n")
    finally:
        os.umask(previous_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site-packages", required=True)
    parser.add_argument("--scripts", default="/usr/local/bin")
    parser.add_argument("--headers", default="/usr/local/include")
    parser.add_argument("--data", default="/usr/local")
    parser.add_argument("--interpreter", default="/usr/bin/python3")
    parser.add_argument("--metadata-output", action="append", type=Path)
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    build_layer(args.wheels, args.output, args.site_packages, args.scripts, args.headers, args.data,
                args.interpreter, metadata_outputs=args.metadata_output)


if __name__ == "__main__":
    main()
