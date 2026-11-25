"""Run the original changelog helper without constructing a Debian package."""

import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

from runtime_packages import DebianTools, PACKAGE


def install(spec):
    package = spec["package"]
    if not PACKAGE.fullmatch(package):
        raise ValueError("invalid Debian package name")
    inputs = {name: Path(path).absolute() for name, path in spec["debian_files"].items()}
    outputs = {name: Path(path).absolute() for name, path in spec["outputs"].items()}
    runtime = Path(spec["runtime"]).absolute()
    upstream = Path(spec["upstream"]).absolute() if spec["upstream"] else None
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name, source in inputs.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Debian metadata path escapes staging directory")
            destination = root / "debian" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        (root / "debian" / package).mkdir()
        tools = DebianTools(runtime, root, spec["architecture"])
        arguments = ["-p" + package, *spec["helper_args"]]
        if upstream:
            arguments.append(str(upstream))
        tools.run("dh_installchangelogs", *arguments)
        installed = root / "debian" / package / "usr/share/doc" / package
        for name, output in outputs.items():
            if PurePosixPath(name).name != name:
                raise ValueError("output must name an installed changelog file")
            source = installed / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("helper did not install the requested regular file: " + name)
            shutil.copyfile(source, output)


if __name__ == "__main__":
    install(json.loads(Path(sys.argv[1]).read_text()))
