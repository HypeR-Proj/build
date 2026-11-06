# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem.composition import source_targets, toml_document
from buildsystem.editor import publish_settings
from buildsystem.model import Module


class EditorSettings(unittest.TestCase):
    def test_ignored_source_files_do_not_become_automatic_cargo_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'src/bin').mkdir(parents=True)
            library, ignored = source / 'src/lib.rs', source / 'src/bin/debug.rs'
            library.write_text('')
            ignored.write_text('fn main() {}')
            result = source_targets({'package': {'name': 'sample', 'edition': '2024'}}, source, {library})
            self.assertNotIn('bin', result)
            self.assertEqual(result['lib']['path'], str(library.resolve()))

    def test_source_entry_uses_prepared_identities_and_repairs_its_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, view = root / 'build/system', root / 'custom-output/workspace/riscv64'
            (view / '.vscode').mkdir(parents=True)
            settings = {'editor.tabSize': 8, 'rust-analyzer.linkedProjects': ['app/tool/Cargo.toml'],
                        'rust-analyzer.cargo.configPath': '.vscode/rust-analyzer.toml'}
            (view / '.vscode/settings.json').write_text(json.dumps(settings))
            owner = Module('build', source, Path('build/system'), {'workspace': {'editor': {
                'settings': 'editor/settings.json'}}})
            publish_settings(view, {'build': owner})
            link = source / 'editor/settings.json'
            self.assertTrue(link.is_symlink())
            actual = json.loads(link.read_text())
            self.assertEqual(actual['editor.tabSize'], 8)
            self.assertEqual(actual['rust-analyzer.linkedProjects'], [str(view / 'app/tool/Cargo.toml')])
            self.assertEqual(actual['rust-analyzer.cargo.configPath'], str(view / '.vscode/rust-analyzer.toml'))
            before = link.lstat().st_mtime_ns
            publish_settings(view, {'build': owner})
            self.assertEqual(before, link.lstat().st_mtime_ns)
            link.unlink()
            publish_settings(view, {'build': owner})
            self.assertEqual(json.loads(link.read_text()), actual)


@unittest.skipUnless(shutil.which('cargo'), 'Cargo is required for target discovery equivalence')
class CargoSourceTargets(unittest.TestCase):
    def test_prepared_targets_match_cargo_discovery_and_original_source_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, output = root / 'source', root / 'view'
            for name in ('src/lib.rs', 'src/main.rs', 'build.rs', 'src/bin/helper.rs',
                         'src/bin/nested/main.rs', 'tests/integration.rs', 'tests/nested/main.rs',
                         'examples/sample.rs', 'benches/benchmark.rs'):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fn main() {}\n')
            output.mkdir()
            base = '[package]\nname="sample"\nversion="0.1.0"\nedition="2024"\n'
            variants = [base, base.replace('2024', '2015'), base + 'autotests=false\nautobins=false\n',
                        base + '\n[[bin]]\nname="renamed"\npath="src/main.rs"\ntest=false\n',
                        base + '\n[[example]]\nname="sample"\ncrate-type=["lib"]\n',
                        base.replace('2024', '2015') + '\n[[bin]]\nname="sample"\n']
            environment = {**os.environ, 'CARGO_NET_OFFLINE': 'true'}
            def metadata(directory):
                result = subprocess.check_output(['cargo', 'metadata', '--no-deps', '--format-version=1'],
                                                 cwd=directory, env=environment, stderr=subprocess.PIPE)
                return json.loads(result)['packages'][0]['targets']
            for manifest in variants:
                with self.subTest(manifest=manifest):
                    (source / 'Cargo.toml').write_text(manifest)
                    expected = metadata(source)
                    (output / 'Cargo.toml').write_text(toml_document(
                        source_targets(tomllib.loads(manifest), source)))
                    actual = metadata(output)
                    order = lambda target: (target['kind'], target['name'])
                    self.assertEqual(sorted(actual, key=order), sorted(expected, key=order))
                    self.assertTrue(all(Path(target['src_path']).is_relative_to(source) for target in actual))


if __name__ == '__main__':
    unittest.main()
