"""Public image tools execute their real CLI with an empty ambient PATH.

The CLI names and required shell applet follow the image assembly consumers:
filesystem creation/readback, Docker store, bootloader, signing and initramfs.
This catches merged-usr wrapper corruption and undeclared loader dependencies.
"""
import argparse
import os
from pathlib import Path
import subprocess
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--tool', action='append', default=[])
arguments, tests = parser.parse_known_args()
TOOLS = dict(value.split('=', 1) for value in arguments.tool)

PROBES = {
    'pigz': (['--version'], b'pigz'),
    'mksquashfs': (['-version'], b'mksquashfs'),
    'unsquashfs': (['-help'], b'unsquashfs'),
    'qemu-img': (['--version'], b'qemu-img'),
    'qemu-system-x86_64': (['--version'], b'QEMU emulator'),
    'sgdisk': (['--version'], b'GPT fdisk'),
    'mke2fs': (['-V'], b'mke2fs'),
    'debugfs': (['-V'], b'debugfs'),
    'mkfs.fat': (['--help'], b'mkfs.fat'),
    'mcopy': (['-V'], b'mcopy'),
    'mmd': (['-V'], b'mmd'),
    'tar-split': (['--help'], b'tar-split'),
    'grub-mkimage': (['--version'], b'grub-mkimage'),
    'grub-editenv': (['--version'], b'grub-editenv'),
    'grub-bios-setup': (['--version'], b'grub-bios-setup'),
    'openssl': (['version'], b'OpenSSL'),
    'bwrap': (['--version'], b'bubblewrap'),
    'zstd': (['--version'], b'Zstandard'),
    'depmod': (['--version'], b'kmod'),
    'modprobe': (['--version'], b'kmod'),
    'busybox': (['--list-full'], b'bin/sh'),
    'mkpasswd': (['--version'], b'mkpasswd'),
}


class RuntimeTest(unittest.TestCase):
    def test_password_hash_matches_libxcrypt_known_answer(self):
        if 'mkpasswd' not in TOOLS:
            self.skipTest('mkpasswd target was not selected')
        # libxcrypt test/ka-table.inc yescrypt known-answer vector.
        result = subprocess.run([str(Path(TOOLS['mkpasswd']).absolute()),
                                 '--method=yescrypt', '--salt=$y$j75$LdJMENpBABJJ3hIHjB1Bi.', '--stdin'],
                                input=b'abc\n', env={**os.environ, 'PATH': ''}, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b'$y$j75$LdJMENpBABJJ3hIHjB1Bi.$aN8IREm4mVoXwP2ZkEbfFinwVjML6Krr5zkB50rSJX3')

    def test_public_tools_run_declared_programs(self):
        self.assertTrue(TOOLS, 'the native test must receive the declared executable targets')
        for name, path in TOOLS.items():
            with self.subTest(tool=name):
                flags, expected = PROBES[name]
                # Preserve the runfiles path of a public executable target.
                executable = str(Path(path).absolute())
                result = subprocess.run([executable, *flags], env={**os.environ, 'PATH': ''},
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
                self.assertIn(expected, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main(argv=[__file__, *tests])
