"""Archive declared control files directly from their producer's package trees."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile


def project(spec, output, receipt):
    package = json.loads(Path(spec['metadata']).read_text())['packages'][spec['package']]
    controls = package['control_files']
    if 'control' not in controls:
        raise ValueError('package producer lacks its Debian control file')
    with tarfile.open(output, 'w', format=tarfile.PAX_FORMAT) as archive:
        for name, record in sorted(controls.items()):
            path = PurePosixPath(record['path'])
            if path.is_absolute() or '..' in path.parts or PurePosixPath(name).name != name:
                raise ValueError('control file escapes its declared package tree')
            candidates = [Path(tree) / str(path) for tree in spec['trees']]
            candidates = [candidate for candidate in candidates if candidate.is_file()]
            if len(candidates) != 1:
                raise ValueError('control file must have one declared tree owner: ' + name)
            content = candidates[0].read_bytes()
            if hashlib.sha256(content).hexdigest() != record['sha256']:
                raise ValueError('control producer hash differs: ' + name)
            member = tarfile.TarInfo(name)
            member.mode, member.size = int(record['mode'], 8), len(content)
            archive.addfile(member, io.BytesIO(content))
    Path(receipt).write_text(json.dumps({'schema': 1, 'package': package['control_fields'],
        'payload_sha256': package['payload_sha256'], 'controls': controls}, indent=2) + '\n')


if __name__ == '__main__':
    project(json.loads(Path(sys.argv[1]).read_text()), *sys.argv[2:])
