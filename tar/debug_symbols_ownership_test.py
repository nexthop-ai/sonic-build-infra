"""Check actual debug_symbols_tar headers, including synthesized directories."""

import sys
import tarfile


with tarfile.open(sys.argv[1], "r:") as archive:
    members = archive.getmembers()
    assert members, "debug-symbol tar is empty"
    assert any(member.isdir() for member in members), "missing synthesized directories"
    symbols = [member for member in members if member.isfile()]
    expected = {
        "usr/lib/debug/.build-id/ab/fixture.debug": b"fixture symbols\n",
        "usr/lib/debug/.build-id/cd/tree with space.debug": b"tree symbols\n",
    }
    assert {m.name.removeprefix("./") for m in symbols} == set(expected)
    for member in symbols:
        assert archive.extractfile(member).read() == expected[member.name.removeprefix("./")]
        assert member.mode == 0o644, (member.name, oct(member.mode))
        assert member.mtime == 1672560000
    links = [member for member in members if member.issym()]
    assert len(links) == 1, links
    assert links[0].name == "./usr/share/doc/fixture-dbgsym"
    assert links[0].linkname == "fixture"
    assert links[0].mtime == 0
    assert links[0].mode == 0o777
    for member in members:
        if member.isdir():
            assert member.mode == 0o755, (member.name, oct(member.mode))
        assert member.uid == member.gid == 0, (member.name, member.uid, member.gid)
        assert member.uname == member.gname == "", (member.name, member.uname, member.gname)

with tarfile.open(sys.argv[2], "r:") as archive:
    assert archive.getmembers() == [], "empty symbol inputs must produce an empty archive"
