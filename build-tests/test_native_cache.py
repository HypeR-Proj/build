# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem.native_cache import Cache


class NativeCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native cache ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / 'Cargo.toml'
        self.manifest.write_text('[workspace]\n')
        self.source = self.root / 'src.rs'
        self.source.write_text('fn main() {}')
        self.external = self.root / 'included data.txt'
        self.external.write_text('old')
        self.binary = self.root / 'program'
        self.binary.write_bytes(b'ELF fixture')
        self.binary.with_suffix('.d').write_text('program: src.rs included\\ data.txt\n')
        self.log = self.root / 'artifacts.jsonl'
        self.log.write_text(json.dumps({'reason': 'compiler-artifact',
                                       'target': {'name': 'program'}, 'filenames': [str(self.binary)]}) + '\n')
        self.command = ['hyper-cargo', 'build', '--manifest-path', str(self.manifest)]
        self.environment = {'HYPER_LINK_MODE': 'dynamic'}
        self.cache = Cache.__new__(Cache)
        self.cache.directory = self.root / 'state'
        self.cache.memo = {}
        self.cache.controls = 'test'
        self.cache.inventory = {'program': [self.source, self.manifest]}
        self.cache.modules = {'program': SimpleNamespace(name='program', source=self.root)}
        self.cache.publish('program', self.command, self.environment, self.log,
                           {'program': self.cache.source('program')})

    def restored(self):
        return self.cache.restore('program', self.command, self.environment, self.log)

    def test_unchanged_build_reuses_completed_artifacts(self):
        self.assertTrue(self.restored())

    def test_depfile_input_change_with_preserved_timestamp_invalidates(self):
        previous = self.external.stat()
        self.external.write_text('new')
        os.utime(self.external, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertFalse(self.restored())

    def test_corrupt_output_and_missing_output_are_not_reused(self):
        self.binary.write_bytes(b'bad fixture')
        self.assertFalse(self.restored())
        self.binary.unlink()
        self.assertFalse(self.restored())

    def test_configuration_and_log_changes_invalidate(self):
        self.environment['HYPER_LINK_MODE'] = 'static'
        self.assertFalse(self.restored())
        self.environment['HYPER_LINK_MODE'] = 'dynamic'
        self.log.write_text('')
        self.assertFalse(self.restored())

    def test_changing_source_during_compile_does_not_publish_success(self):
        before = {'program': self.cache.source('program')}
        self.source.write_text('fn main() { /* changed */ }')
        with self.assertRaisesRegex(ValueError, 'changed during compilation'):
            self.cache.publish('program', self.command, self.environment, self.log, before)
        self.assertFalse(self.restored())

    def test_graph_cache_requires_matching_controls_and_valid_body(self):
        calls = []
        def create():
            calls.append(True)
            return {'version': 1, 'units': [], 'roots': []}
        self.cache.graph(self.command, self.environment, create)
        self.cache.graph(self.command, self.environment, create)
        self.assertEqual(len(calls), 1)
        path = next((self.cache.directory / 'graphs').glob('*.json'))
        value = json.loads(path.read_text())
        value['graph']['version'] = 2
        path.write_text(json.dumps(value))
        self.cache.graph(self.command, self.environment, create)
        self.assertEqual(len(calls), 2)
        self.manifest.write_text('[workspace]\nresolver = "3"\n')
        self.cache.graph(self.command, self.environment, create)
        self.assertEqual(len(calls), 3)

    def test_fetch_rechecks_changed_locks_and_removed_registry_data(self):
        environment = {**self.environment, 'CARGO_HOME': str(self.root / 'cargo')}
        archive = self.root / 'cargo/registry/cache/example/package.crate'
        calls = []
        def fetch(command):
            calls.append(command)
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b'package')
        self.cache.fetch([self.command], environment, fetch)
        self.cache.fetch([self.command], environment, fetch)
        self.assertEqual(len(calls), 1)
        (self.root / 'Cargo.lock').write_text('version = 4\n')
        self.cache.fetch([self.command], environment, fetch)
        self.assertEqual(len(calls), 2)
        archive.unlink()
        self.cache.fetch([self.command], environment, fetch)
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
