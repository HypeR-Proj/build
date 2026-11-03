# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem.model import Module
from buildsystem.native import Builder


class NativeSources(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / 'out'
        self.archive = self.root / 'upstream.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as bundle:
            entry = tarfile.TarInfo('source.cc')
            entry.size = 7
            bundle.addfile(entry, io.BytesIO(b'payload'))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.modules = {}
        for name in ('consumer', 'dependency', 'unused'):
            source = self.root / name
            source.mkdir()
            (source / 'lib.rs').write_text('')
            metadata = {}
            if name != 'consumer':
                metadata['build'] = {'type': 'native-rust', 'source_archives': {
                    'manifest': 'sources.json', 'cache': name,
                    'cache_env': 'HYPER_' + name.upper() + '_SOURCE_CACHE'}}
                (source / 'sources.json').write_text(json.dumps({'archives': [{
                    'path': 'upstream', 'sha256': self.digest,
                    'url': 'https://invalid.example/upstream.tar.gz'}]}))
            self.modules[name] = Module(name, source, Path(name), metadata)
        self.builder = Builder(self.root, self.modules, self.output, 'aarch64', self.root / 'sdk')
        self.builder._environment = dict(os.environ)
        self.builder._cache = Mock()
        self.builder._cache.restore.return_value = False
        self.graph = {'units': [{'target': {'src_path': str(self.modules[name].source / 'lib.rs')}}
                                for name in ('consumer', 'dependency')]}
        self.builder.graph = Mock(return_value=self.graph)

    def build(self):
        compiler = ('import os; from pathlib import Path; '
                    'p=Path(os.environ["HYPER_DEPENDENCY_SOURCE_CACHE"]); '
                    'files=list(p.glob("unpacked/*/source.cc")); '
                    'assert len(files)==1 and files[0].read_bytes()==b"payload"; '
                    'print("{}")')
        return self.builder.run([sys.executable, '-c', compiler], 'system', 'std', 'consumer', 'build')

    def downloader(self, command, **kwargs):
        if command[0] == 'curl':
            shutil.copyfile(self.archive, command[command.index('-o') + 1])
            return subprocess.CompletedProcess(command, 0)
        return self.real_run(command, **kwargs)

    def test_cold_dependency_prepared_before_compiler_and_unused_module_not_fetched(self):
        self.real_run = subprocess.run
        with patch('source_archive.subprocess.run', side_effect=self.downloader):
            self.build()
        self.assertTrue((self.output / 'cache/dependency/archives' / (self.digest + '.tar.gz')).is_file())
        self.assertFalse((self.output / 'cache/unused').exists())
        environment = self.builder.cache.publish.call_args.args[2]
        self.assertEqual(environment['HYPER_DEPENDENCY_SOURCE_CACHE'], str(self.output / 'cache/dependency'))

    def test_cached_artifact_does_not_refetch_removed_archives(self):
        self.builder.cache.restore.return_value = True
        with patch('source_archive.subprocess.run', side_effect=AssertionError('unexpected fetch/compiler')):
            self.build()
        self.assertFalse((self.output / 'cache').exists())

    def test_modified_extracted_dependency_stops_before_compiler(self):
        self.real_run = subprocess.run
        with patch('source_archive.subprocess.run', side_effect=self.downloader):
            self.build()
        file = next((self.output / 'cache/dependency/unpacked').glob('*/source.cc'))
        file.write_bytes(b'changed')
        self.builder.cache.publish.reset_mock()
        with patch('source_archive.subprocess.run', side_effect=AssertionError('unexpected compiler')):
            with self.assertRaisesRegex(ValueError, 'extracted source'):
                self.build()
        self.builder.cache.publish.assert_not_called()

    def test_cache_override_and_symlinked_sources_follow_dependency_owner(self):
        linked = self.root / 'view'
        linked.symlink_to(self.modules['dependency'].source, target_is_directory=True)
        self.graph['units'][1]['target']['src_path'] = str(linked / 'lib.rs')
        environment = {'HYPER_DEPENDENCY_SOURCE_CACHE': 'alternate-cache'}
        inputs = self.builder.source_inputs(self.graph, environment)
        self.assertEqual(inputs, [(self.modules['dependency'].source / 'sources.json', self.root / 'alternate-cache')])
        self.assertEqual(environment['HYPER_DEPENDENCY_SOURCE_CACHE'], str(self.root / 'alternate-cache'))

    def test_bad_archive_pin_stops_before_compiler(self):
        manifest = self.modules['dependency'].source / 'sources.json'
        manifest.write_text(manifest.read_text().replace(self.digest, '0' * 64))
        with patch('source_archive.subprocess.run', side_effect=self.downloader) as run:
            with self.assertRaisesRegex(ValueError, 'source digest mismatch'):
                self.build()
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ['curl'])
        self.builder.cache.publish.assert_not_called()

    def test_prepared_view_with_linked_source_files_fetches_dependency(self):
        module = self.modules['dependency']
        view = self.root / 'prepared/dependency'
        view.mkdir(parents=True)
        for name in ('lib.rs', 'sources.json'):
            (view / name).symlink_to(module.source / name)
        self.modules['dependency'] = Module(module.name, view, Path('dependency'), module.metadata)
        self.graph['units'][1]['target']['src_path'] = str(view / 'lib.rs')
        self.real_run = subprocess.run
        with patch('source_archive.subprocess.run', side_effect=self.downloader):
            self.build()
        self.assertTrue((self.output / 'cache/dependency/archives' / (self.digest + '.tar.gz')).is_file())


if __name__ == '__main__':
    unittest.main()
