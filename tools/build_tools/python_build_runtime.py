"""Prepare declared DTrace and production CPython profile-training inputs."""
import json
import os
from pathlib import Path


def prepare_python_build(root, architecture, loader, libraries, launcher):
    from prepare_runtime import run_tool, write_launcher
    (root / "bin").mkdir(exist_ok=True)
    tools = {"python3": "usr/bin/python3.13"}
    for name in ("dtrace", "dash"):
        if (root / "usr/bin" / name).is_file():
            tools[name] = "usr/bin/" + name
    tools["sh"] = "usr/bin/dash"
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    # Preserve caller values while exposing the original read-only build data.
    data_environment = {}
    for name, relative, landmark in [
        ("LOCPATH", "usr/lib/locale", "usr/lib/locale"),
        ("TCL_LIBRARY", "usr/share/tcltk/tcl8.6", "usr/share/tcltk/tcl8.6/init.tcl"),
        ("TK_LIBRARY", "usr/share/tcltk/tk8.6", "usr/share/tcltk/tk8.6/tk.tcl"),
        ("TZDIR", "usr/share/zoneinfo", "usr/share/zoneinfo"),
        ("PYTHONTZPATH", "usr/share/zoneinfo", "usr/share/zoneinfo"),
    ]:
        if (root / landmark).exists():
            data_environment[name] = relative
    prefix = [str(loader), "--inhibit-cache", "--library-path", os.pathsep.join(map(str, libraries))]
    for name, relative in tools.items():
        target = root / relative
        if not target.is_file():
            raise ValueError("declared Python build runtime has no " + relative)
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
PATH="$root/bin${PATH:+:$PATH}"
export PATH
'''
        for variable, data_relative in data_environment.items():
            script += 'if [ "${' + variable + '+x}" != x ]; then ' + variable + '="$root/' + data_relative + '"; fi\nexport ' + variable + '\n'
        command = 'exec "$root/' + str(loader.relative_to(root)) + '" --inhibit-cache --library-path "' + library_path + '" '
        if name in ("dtrace", "python3"):
            script += 'PYTHONHOME="$root/usr"\nPYTHONNOUSERSITE=1\nPYTHONPATH="$root/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"\nexport PYTHONHOME PYTHONNOUSERSITE PYTHONPATH\n'
            command += '--argv0 "$root/bin/python3" "$root/usr/bin/python3.13" -B '
            if name == "dtrace":
                command += '"$root/' + relative + '" '
        else:
            command += '"$root/' + relative + '" '
        script += command + '"$@"\n'
        write_launcher(root, name, script, launcher)
        if target.read_bytes().startswith(b"\x7fELF"):
            listing = run_tool(prefix + ["--list", str(target)], environment={"LANG": "C", "PATH": ""}).decode()
            if "not found" in listing:
                raise ValueError("incomplete declared runtime for " + name + ":\n" + listing)
            import re
            for path in re.findall(r"=>\s+(/\S+)", listing):
                if not Path(path).resolve().is_relative_to(root):
                    raise ValueError(name + " resolved an undeclared library: " + path)
    dangling = [p for p in root.rglob("*") if p.is_symlink() and not p.exists()]
    for p in dangling:
        p.unlink()
    manifest = {"format_version": 1, "profile": "python_build", "architecture": architecture, "loader": str(loader.relative_to(root)), "library_directories": [str(p.relative_to(root)) for p in libraries], "tools": tools, "data_environment": data_environment, "path_policy": "declared bin prefix plus unchanged caller suffix"}
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")
