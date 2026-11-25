"""Check installed archive semantics without executing target object code."""
import argparse
import json
from pathlib import Path

from elf_metadata import Elf


def members(path):
    data = path.read_bytes()
    assert data.startswith(b"!<arch>\n"), f"Expected a regular ar archive: {path}"
    offset = 8
    long_names = b""
    result = []
    while offset < len(data):
        header = data[offset:offset + 60]
        assert len(header) == 60 and header[58:60] == b"`\n", path
        size = int(header[48:58])
        name = header[:16].strip()
        content = data[offset + 60:offset + 60 + size]
        assert len(content) == size, path
        offset += 60 + size + size % 2
        if name == b"//":
            long_names = content
            continue
        if name in (b"/", b"/SYM64/") or name.startswith(b"__.SYMDEF"):
            continue
        if name.startswith(b"#1/"):
            length = int(name[3:])
            name, content = content[:length].rstrip(b"\0"), content[length:]
        elif name.startswith(b"/") and name[1:].isdigit():
            start = int(name[1:])
            end = long_names.index(b"/\n", start)
            name = long_names[start:end]
        else:
            name = name.rstrip(b"/")
        assert content.startswith(b"\x7fELF"), f"Expected an ELF archive member: {name!r}"
        result.append((name.decode(), Elf(content)))
    return result


def global_definitions(elf):
    table = elf.by_name.get(".symtab")
    assert table is not None, "A linkable object must retain its symbol table"
    strings = elf.section_data(elf.sections[table[6]])
    symbols = {}
    for offset in range(table[4], table[4] + table[5], table[9]):
        symbol = elf.unpack("IBBHQQ" if elf.bits == 64 else "IIIBBH", offset)
        info = symbol[1] if elf.bits == 64 else symbol[3]
        visibility = (symbol[2] if elf.bits == 64 else symbol[4]) & 3
        defined = symbol[3] if elf.bits == 64 else symbol[5]
        if defined and info >> 4 in (1, 2):
            symbols[elf.string(strings, symbol[0])] = (info >> 4, info & 15, visibility)
    return symbols


def has_dwarf(elf):
    return any(name.startswith((".debug_", ".zdebug_")) for name in elf.by_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--stripped", type=Path, required=True)
    parser.add_argument("--consumer", type=Path, required=True)
    parser.add_argument("--machine", type=int, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    before, after = members(args.input), members(args.stripped)
    assert len(before) >= 2, "The fixture must exercise a multi-member archive"
    assert [name for name, _ in before] == [name for name, _ in after], "Archive members changed"
    definitions = set()
    for (name, original), (_, stripped) in zip(before, after):
        assert original.machine == stripped.machine == args.machine, name
        assert has_dwarf(original), f"Input fixture has no DWARF: {name}"
        assert not has_dwarf(stripped), f"Installed archive retained DWARF: {name}"
        original_symbols = global_definitions(original)
        assert global_definitions(stripped) == original_symbols, f"Global symbols changed: {name}"
        definitions.update(original_symbols)
    required = {"archive_sum", "archive_product"}
    assert required <= definitions, definitions
    consumer = Elf(args.consumer.read_bytes())
    assert consumer.machine == args.machine, "Linked consumer architecture changed"
    assert required <= global_definitions(consumer).keys(), "Consumer did not link both archive functions"
    report = {
        "machine": args.machine,
        "members": [name for name, _ in after],
        "preserved_global_symbols": sorted(definitions),
        "input_has_dwarf": True,
        "output_has_dwarf": False,
        "consumer_linked_symbols": sorted(required),
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
