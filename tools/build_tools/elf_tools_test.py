"""Exercise selected APT tool CLIs through their public executable/runfiles API."""
import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

parser = argparse.ArgumentParser()
for name in ('shellcheck', 'pigz', 'gzip-wrapper', 'dwp', 'patchelf', 'binary'):
    parser.add_argument('--' + name)
TOOLS, remaining = parser.parse_known_args()


def run(name, arguments, data=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith('LD_')}
    # The rules_multirun wrapper uses the project's permitted Bash/Coreutils;
    # the underlying public APT executable must need no ambient PATH.
    if name != 'gzip_wrapper':
        environment['PATH'] = ''
    return subprocess.run([str(Path(getattr(TOOLS, name)).absolute()), *arguments],
                          input=data, capture_output=True, env=environment, timeout=30)


class ToolConsumerTest(unittest.TestCase):
    def test_selected_declared_tools(self):
        self.assertTrue(any(getattr(TOOLS, name) for name in ('shellcheck', 'pigz', 'dwp', 'patchelf')))
        if TOOLS.shellcheck:
            valid = run('shellcheck', ['--shell=sh', '-'], b'#!/bin/sh\nprintf "%s\\n" "$1"\n')
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            invalid = run('shellcheck', ['--shell=sh', '-'], b'#!/bin/sh\necho $1\n')
            self.assertNotEqual(invalid.returncode, 0)
        if TOOLS.pigz:
            data = b'declared source installation bytes\x00' * 1024
            for name in ('pigz', 'gzip_wrapper'):
                first = run(name, ['-n', '-c'], data)
                second = run(name, ['-n', '-c'], data)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(first.stdout, second.stdout)
                self.assertEqual(first.stdout[4:8], b'\x00' * 4)
                self.assertEqual(first.stdout[3] & 8, 0)
                self.assertEqual(gzip.decompress(first.stdout), data)
        if TOOLS.dwp:
            result = run('dwp', ['--version'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'GNU dwp', result.stdout)
        if TOOLS.patchelf:
            source = Path(TOOLS.binary)
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            with tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'program'
                shutil.copyfile(source, target)
                wanted = '/declared/test/runtime'
                result = run('patchelf', ['--set-rpath', wanted, str(target)])
                self.assertEqual(result.returncode, 0, result.stderr)
                result = run('patchelf', ['--print-rpath', str(target)])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().decode(), wanted)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)


if __name__ == '__main__':
    unittest.main(argv=[__file__, *remaining])
