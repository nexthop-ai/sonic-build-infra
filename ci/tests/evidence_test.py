"""Exercise public artifact allowlists with synthetic raw command/env records."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ('bazel.yml', 'declared-build-tools.yml', 'shared-rust-deps.yml')


def upload_paths(workflow):
    """Read only upload-artifact path scalars from these maintained workflows."""
    paths = []
    for step in workflow.read_text().split('\n      - '):
        if 'uses: actions/upload-artifact@' not in step:
            continue
        match = re.search(r'^          path: (.+)$', step, re.MULTILINE)
        if not match:
            raise AssertionError('An artifact upload has no explicit path')
        if match[1] == '|':
            tail = step[match.end():].splitlines()
            for line in tail:
                if line.startswith('            '):
                    paths.append(line.strip())
                elif line.strip():
                    break
        else:
            paths.append(match[1])
    return paths


class EvidenceTest(unittest.TestCase):
    def test_raw_environment_and_commands_never_match_upload_paths(self):
        # These are deliberately fabricated values, not runner credentials.
        sentinel = 'SYNTHETIC_CREDENTIAL_MUST_REMAIN_PRIVATE'
        raw = json.dumps({'unstructuredCommandLine': {'args': [sentinel]},
                          'client_env': {'TEST_TOKEN': sentinel},
                          'environmentVariables': [{'name': 'TEST_TOKEN', 'value': sentinel}]})
        private_paths = [
            'ci-artifacts/actions.json', 'ci-artifacts/apt-python313-actions.json',
            'ci-artifacts/bookworm-actions.json', 'ci-artifacts/tests.bep.json',
            'ci-artifacts/build.bep.json', 'ci-artifacts/build-events.jsonl',
            'ci-artifacts/test-events.jsonl', 'ci-artifacts/cache-test-events.jsonl',
            'ci-artifacts/configure-actions.json', 'ci-artifacts/cache-spawns.json',
            'ci-artifacts/shared-sysroot/gcc/actions.json',
            'ci-artifacts/shared-sysroot/clang/launcher-test-events.jsonl',
            'ci-artifacts/cross-arm64/gcc/actions.json',
            'ci-artifacts/testlogs/package/test/test.raw.env',
            'rust/deps/ci-artifacts/test-events.json',
            'rust/deps/ci-artifacts/ha-dependency-events.json',
            'rust/deps/ci-artifacts/release-events.json',
            'rust/deps/ci-artifacts/cargo-release-actions.json',
            'runner-private/bazel/tests.bep.json',
        ]
        public_paths = {
            'bazel.yml': [
                'ci-artifacts/suite.json', 'ci-artifacts/action-audit.json',
                'ci-artifacts/testlogs/package/test/test.xml',
                'ci-artifacts/testlogs/package/test/test.log',
                'ci-artifacts/outputs/hello_deploy_tar.tar',
                'ci-artifacts/outputs/hello_deploy_tar.debug_symbols.tar',
                'ci-artifacts/shared-sysroot/validation.json',
                'ci-artifacts/cross-arm64/runtime-bundle.tar.gz',
                'ci-artifacts/cross-runtime/validation.json',
            ],
            'declared-build-tools.yml': [
                'ci-artifacts/suite.json', 'ci-artifacts/cache-reuse.json',
                'ci-artifacts/launcher_action_check.json',
                'ci-artifacts/initial-testlogs/kernel_runtime_tools_test/test.xml',
                'ci-artifacts/testlogs/kernel_runtime_tools_test/test.log',
                'ci-artifacts/libtool/compiler-argv-validation.json',
            ],
            'shared-rust-deps.yml': [
                'rust/deps/ci-artifacts/cargo-release-profile.json',
                'rust/deps/ci-artifacts/cargo-release-metadata.json',
                'rust/deps/ci-artifacts/serde-test.xml',
                'rust/deps/ci-artifacts/Cargo.lock',
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in private_paths:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(raw)
            for paths in public_paths.values():
                for path in paths:
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('safe fixture evidence\n')
            for name in WORKFLOWS:
                with self.subTest(workflow=name):
                    patterns = upload_paths(ROOT / '.github/workflows' / name)
                    self.assertTrue(patterns)
                    selected = set()
                    for pattern in patterns:
                        self.assertFalse(pattern.endswith(('/', '**', '*')), pattern)
                        for path in root.glob(pattern):
                            self.assertTrue(path.is_file(), path)
                            selected.add(path.relative_to(root).as_posix())
                            self.assertNotIn(sentinel, path.read_text(), path)
                    self.assertTrue(set(public_paths[name]).issubset(selected))
                    self.assertFalse(set(private_paths) & selected)

    def test_restored_cache_receipt_drops_environment_and_rejects_misses(self):
        workflow = (ROOT / '.github/workflows/declared-build-tools.yml').read_text()
        body = workflow.split("          python3 - <<'PYTHON'\n", 1)[1].split("          PYTHON", 1)[0]
        body = textwrap.dedent(body)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / 'private'
            raw.mkdir()
            (root / 'ci-artifacts').mkdir()
            receipt = root / 'ci-artifacts/cache-reuse.json'
            for architecture in ['amd64', 'arm64']:
                for cache_hit in [True, False]:
                    with self.subTest(architecture=architecture, cache_hit=cache_hit):
                        records = []
                        for mnemonic, label in [('PrepareBuildTools', '//tools/build_tools:configure_runtime'),
                                                ('PrepareBuildToolsRootfs', '//tools/build_tools:kernel_runtime_' + architecture)]:
                            records.append({'mnemonic': mnemonic, 'targetLabel': label,
                                            'listedOutputs': ['runtime'], 'runner': 'disk cache hit' if cache_hit else 'local',
                                            'cacheHit': cache_hit, 'environmentVariables': ['SYNTHETIC_PRIVATE_TOKEN'],
                                            'commandArgs': ['SYNTHETIC_PRIVATE_TOKEN']})
                        (raw / 'cache-spawns.json').write_text('\n'.join(json.dumps(record) for record in records))
                        receipt.unlink(missing_ok=True)
                        result = subprocess.run([sys.executable, '-c', body], cwd=root,
                                                env=dict(os.environ, CI_RAW_DIR=str(raw), ARCHITECTURE=architecture),
                                                capture_output=True, text=True)
                        self.assertEqual(result.returncode == 0, cache_hit, result.stderr)
                        self.assertEqual(receipt.exists(), cache_hit)
                        if cache_hit:
                            public = json.loads(receipt.read_text())
                            self.assertEqual(len(public), 2)
                            self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN', receipt.read_text())
                            self.assertEqual({row['mnemonic'] for row in public},
                                             {'PrepareBuildTools', 'PrepareBuildToolsRootfs'})

    def test_action_audit_publishes_counts_and_still_rejects_debs(self):
        script = (ROOT / 'ci/bazel-ci.sh').read_text()
        body = script.split("python3 - \"$raw\" \"$artifacts\" <<'PYTHON'\n", 1)[1].split('\nPYTHON', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, public = root / 'private', root / 'public'
            private.mkdir()
            public.mkdir()
            for extension in ['tar', 'deb']:
                graph = {'pathFragments': [{'id': 1, 'label': 'output.' + extension}],
                         'artifacts': [{'id': 1, 'pathFragmentId': 1}],
                         'actions': [{'mnemonic': 'FixtureAction', 'outputIds': [1],
                                      'arguments': ['fixture-tool', 'SYNTHETIC_PRIVATE_TOKEN'],
                                      'environmentVariables': ['SYNTHETIC_PRIVATE_TOKEN']}]}
                for name in ['actions.json', 'apt-python313-actions.json', 'bookworm-actions.json']:
                    (private / name).write_text(json.dumps(graph))
                result = subprocess.run([sys.executable, '-c', body, str(private), str(public)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, extension == 'tar')
                receipt = json.loads((public / 'action-audit.json').read_text())
                self.assertEqual(receipt['action_count'], 1)
                self.assertEqual(receipt['deb_outputs'], ['output.deb'] if extension == 'deb' else [])
                self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN', json.dumps(receipt))


if __name__ == '__main__':
    unittest.main()
