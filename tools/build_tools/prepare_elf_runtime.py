"""Prepare only explicitly selected APT ELFs using the existing runtime helpers."""
import argparse
import json
import os
from pathlib import Path
import re
from elf_metadata import Elf
from launcher import has_interpreter
from prepare_runtime import (extract_payload, library_directories, loader_path,
                             run_tool, safe_path, validate_launcher, write_launcher)


def prepare_elf_tools(root, architecture, loader, libraries, executable, paths):
    """Expose explicitly selected locked APT ELFs without an unrelated tool set."""
    if not paths:
        raise ValueError("elf profile requires explicit tool paths")
    wrappers = root / "bin"
    if wrappers.is_symlink():
        wrappers.unlink()
    wrappers.mkdir(exist_ok=True)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    for name, path in paths.items():
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name):
            raise ValueError("invalid ELF tool name: " + name)
        target = safe_path(root, path)
        if not target.resolve().is_relative_to(root) or not target.is_file():
            raise ValueError("ELF tool missing from locked packages: " + path)
        elf = Elf(target.read_bytes())
        if elf.bits != 64 or elf.order != '<':
            raise ValueError("tool is not a little-endian ELF64 executable: " + path)
        if elf.machine != {"amd64": 62, "arm64": 183}[architecture]:
            raise ValueError("ELF tool architecture mismatch: " + path)
        dynamic = has_interpreter(target)
        if dynamic:
            listing = run_tool([str(loader), "--library-path", os.pathsep.join(map(str, libraries)), "--list", str(target)], environment={"LANG": "C", "LC_ALL": "C", "PATH": ""}).decode()
            if "not found" in listing:
                raise ValueError("incomplete runtime for " + name + ":\n" + listing)
            for resolved in re.findall(r"=>\s+(/\S+)", listing):
                if not Path(resolved).resolve().is_relative_to(root):
                    raise ValueError(name + " resolved an undeclared library: " + resolved)
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
'''
        if dynamic:
            script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/' + path + '" "$@"\n'
        else:
            script += 'exec "$root/' + path + '" "$@"\n'
        write_launcher(root, name, script, executable)
    # Unconfigured Debian alternatives can refer to unselected optional packages.
    # The selected ELFs and their library closure above remain mandatory.
    dangling = [path for path in root.rglob("*") if path.is_symlink() and not path.exists()]
    omitted = [{"path": str(path.relative_to(root)), "target": os.readlink(path)} for path in dangling]
    for path in dangling:
        path.unlink()
    (root / "toolchain.json").write_text(json.dumps({"format_version": 1, "profile": "elf", "architecture": architecture, "loader": str(loader.relative_to(root)), "library_directories": [str(path.relative_to(root)) for path in libraries], "tools": paths, "omitted_unresolved_symlinks": omitted}, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--architecture', choices=('amd64', 'arm64'), required=True)
    parser.add_argument('--launcher', required=True, type=Path)
    parser.add_argument('--tar', action='append', default=[])
    parser.add_argument('--profile', choices=('elf',), required=True)
    parser.add_argument('--elf-tool', action='append', default=[])
    args = parser.parse_args()
    validate_launcher(args.launcher, args.architecture)
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for payload in sorted(map(Path, args.tar)):
        extract_payload(root, payload)
    paths = {}
    for declaration in args.elf_tool:
        name, separator, path = declaration.partition('=')
        if not separator or name in paths:
            raise ValueError('invalid or duplicate ELF tool declaration: ' + declaration)
        paths[name] = path
    prepare_elf_tools(root, args.architecture, loader_path(root, args.architecture),
                      library_directories(root), args.launcher, paths)


if __name__ == '__main__':
    main()
