"""Original installation layout and GNU build-id/debuglink matching contract."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import zlib

runtime, debug, watchdog, readelf = sys.argv[1:]
readelf = str(Path(readelf).absolute())
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for source, destination in [(runtime, root / "runtime"), (debug, root / "debug"), (watchdog, root / "watchdog")]:
        with tarfile.open(source) as archive:
            for member in archive:
                if member.isfile():
                    assert member.uid == member.gid == 0, (source, member.name)
                    assert member.mode == (0o644 if source == debug else 0o755), (source, member.name, oct(member.mode))
            archive.extractall(destination, filter="data")
    binary = root / "runtime/usr/bin/release-probe"
    binary.chmod(0o755)
    result = subprocess.run([binary], capture_output=True, text=True, check=True)
    assert result.stdout == "release-37\n", result.stdout
    symbols = list((root / "debug/usr/lib/debug/.build-id").rglob("*.debug"))
    assert len(symbols) == 1, symbols
    def read(*args):
        return subprocess.run([readelf, *map(str, args)], capture_output=True, text=True, check=True, env=dict(os.environ, LC_ALL="C")).stdout
    runtime_id = re.search(r"Build ID: ([0-9a-f]+)", read("-n", binary)).group(1)
    debug_id = re.search(r"Build ID: ([0-9a-f]+)", read("-n", symbols[0])).group(1)
    assert runtime_id == debug_id, (runtime_id, debug_id)
    assert str(symbols[0]).endswith("/" + runtime_id[:2] + "/" + runtime_id[2:] + ".debug")
    assert ".debug_info" not in read("-S", binary)
    assert ".debug_info" in read("-S", symbols[0])
    # .gnu_debuglink ends with four arbitrary CRC bytes, not UTF-8 text.
    # readelf's section dump is independent of the producer and retains CRC bytes.
    dump = read("-x", ".gnu_debuglink", binary)
    words = []
    for line in dump.splitlines():
        match = re.match(r"\s+0x[0-9a-f]+\s+((?:[0-9a-f]{8}\s*){1,4})", line)
        if match:
            words.extend(match.group(1).split())
    raw = bytes.fromhex("".join(words))
    assert raw[:raw.index(b"\0")] == symbols[0].name.encode("utf-8")
    offset = (raw.index(b"\0") + 4) & ~3
    byteorder = "little" if "little endian" in read("-h", binary) else "big"
    assert int.from_bytes(raw[offset:offset + 4], byteorder) == zlib.crc32(symbols[0].read_bytes())
    original = root / "watchdog/usr/bin/gnmi_watchdog"
    assert original.is_file()
    assert "ELF64" in read("-h", original)
    print("Verified release fixture execution, original watchdog installation and matching uncompressed symbols.")
