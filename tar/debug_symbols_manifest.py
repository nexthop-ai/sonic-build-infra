"""Map generated mtree entries to installation paths without copying payloads."""

import json
from pathlib import Path, PurePosixPath
import re
import sys


def escape(value):
    return re.sub(r"[\\#=\s]", lambda m: "".join("\\%03o" % byte for byte in m[0].encode()), value)


def unescape(value):
    return re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m[1], 8)]), value.encode()).decode()


def manifest(spec, package, symlinks):
    entries = {}

    def add(path, attrs):
        old = entries.get(path)
        if old is not None and old != attrs:
            raise ValueError("duplicate debug archive path: " + path)
        entries[path] = attrs

    for line in spec.splitlines():
        if not line or line.startswith("#"):
            continue
        source, *words = line.split()
        attrs = dict(word.split("=", 1) for word in words)
        # Source ancestor directories are regenerated from the installed paths.
        if attrs["type"] == "dir":
            continue
        parts = PurePosixPath(unescape(source)).parts
        if ".build-id" in parts:
            relative = PurePosixPath(*parts[parts.index(".build-id"):])
        else:
            relative = PurePosixPath(unescape(source))
            if package and relative.is_relative_to(package):
                relative = relative.relative_to(package)
        if not entries:
            # Preserve the old package_dir ancestor metadata. Empty inputs
            # remain empty archives, matching the collected-tree path.
            for parent in (".", "./usr", "./usr/lib", "./usr/lib/debug"):
                add(parent, "type=dir mode=0755 time=946699200 uid=0 gid=0 uname= gname=")
        path = "./usr/lib/debug/" + str(relative)
        for parent in reversed(PurePosixPath(path).parents):
            if str(parent) in (".", "usr", "usr/lib", "usr/lib/debug"):
                continue
            add("./" + str(parent), "type=dir mode=0755 time=1672560000 uid=0 gid=0 uname= gname=")
        attrs.update(uid="0", gid="0", uname="", gname="", mode="0644")
        add(path, " ".join(key + "=" + value for key, value in attrs.items()))

    for name, target in sorted(symlinks.items()):
        path = PurePosixPath(name.removeprefix("./"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("invalid installation symlink path: " + name)
        add("./" + str(path), "type=link mode=0777 time=0 uid=0 gid=0 uname= gname= link=" + escape(target))

    # Parents precede children; stable ordering also makes the archive independent
    # of the order in which transitive symbol trees were collected.
    return "#mtree\n" + "".join(escape(path) + " " + entries[path] + "\n" for path in sorted(entries))


if __name__ == "__main__":
    source, output, package, links = sys.argv[1:]
    Path(output).write_text(manifest(Path(source).read_text(), package, json.loads(Path(links).read_text())))
