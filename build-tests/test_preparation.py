# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from buildsystem.config import backend
from buildsystem.model import dependency_order, discover
from buildsystem.prepare import execution, prepare


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'source'
        self.output = Path(temporary.name).resolve() / 'artifacts'
        self.root.mkdir()
        for name, path in [('build', '.'), ('alpha', 'modules/alpha'), ('beta', 'modules/beta'),
                           ('manifest', '.repo/manifests')]:
            self.describe(name, path)
        (self.root / '.repo/manifests/default.xml').write_text('''<manifest>
          <project name="build" path="."/>
          <project name="alpha" path="modules/alpha"/>
          <project name="beta" path="modules/beta"/>
        </manifest>''')
        (self.root / '.repo/manifest.xml').symlink_to('manifests/default.xml')
        (self.root / 'policy.py').write_text('original policy\n')

    def describe(self, name, path, **metadata):
        folder = self.root / path
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'component.json').write_text(json.dumps({'name': name, 'version': 1, **metadata}))

    def test_repo_manifest_has_one_owner_without_a_second_checkout(self):
        view, modules, changed, _ = prepare(self.root, self.output)
        self.assertTrue(changed)
        self.assertEqual(modules['manifest'].source, self.root / '.repo/manifests')
        self.assertEqual(set(discover(view)), set(modules))
        self.assertFalse((self.root / 'manifest').exists())
        self.assertEqual((view / 'manifest/default.xml').read_bytes(),
                         (self.root / '.repo/manifests/default.xml').read_bytes())
        self.assertEqual(prepare(self.root, self.output)[2], False)

    def test_generated_controls_are_repaired_without_editing_sources(self):
        view, _, _, _ = prepare(self.root, self.output)
        (view / 'policy.py').write_text('altered generated file\n')
        prepare(self.root, self.output)
        self.assertEqual((view / 'policy.py').read_text(), 'original policy\n')
        self.assertEqual((self.root / 'policy.py').read_text(), 'original policy\n')
        (view / 'policy.py').chmod(0o700)
        prepare(self.root, self.output)
        self.assertEqual((view / 'policy.py').stat().st_mode & 0o777, 0o644)

    def test_invalid_overlay_does_not_publish_part_of_a_new_view(self):
        view, _, _, _ = prepare(self.root, self.output)
        before = (view / 'component.json').read_bytes()
        self.describe('build', '.', workspace={'files': {'absent.py': 'generated.py'}})
        with self.assertRaises(FileNotFoundError):
            prepare(self.root, self.output)
        self.assertEqual((view / 'component.json').read_bytes(), before)
        self.assertFalse((view / 'generated.py').exists())

    def test_output_cannot_be_shared_by_different_source_workspaces(self):
        prepare(self.root, self.output)
        other = self.root.parent / 'another-source'
        other.mkdir()
        (other / 'manifest').symlink_to(self.root / '.repo/manifests', target_is_directory=True)
        for path in ('component.json', 'modules'):
            (other / path).symlink_to(self.root / path)
        with self.assertRaisesRegex(ValueError, 'another workspace'):
            prepare(other, self.output)
        with self.assertRaisesRegex(ValueError, 'must not contain'):
            with execution(self.root, self.root):
                self.fail('invalid output was accepted')
        self.assertFalse((self.root / '.prepare.lock').exists())

    def test_dependency_cycles_and_missing_providers_fail_before_execution(self):
        self.describe('alpha', 'modules/alpha', build={'requires': ['beta']})
        self.describe('beta', 'modules/beta', build={'requires': ['alpha']})
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            prepare(self.root, self.output)
        self.describe('beta', 'modules/beta', build={'requires': ['absent']})
        with self.assertRaisesRegex(ValueError, 'unknown module'):
            dependency_order(discover(self.root), ['alpha'])

    def test_clean_removes_outputs_but_preserves_configs_sources_and_external_links(self):
        from build import execute
        view, modules, changed, _ = prepare(self.root, self.output)
        source = self.root / 'policy.py'
        original = source.read_bytes()
        config = self.output / 'config/aarch64/.config'
        config.parent.mkdir(parents=True)
        config.write_text('CONFIG_EXAMPLE=y\n')
        (self.output / 'sdk').symlink_to(self.root, target_is_directory=True)
        args = SimpleNamespace(action='clean', module=None, module_source=None)
        execute(args, self.root, self.output, 'aarch64', view, modules, changed)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(config.read_text(), 'CONFIG_EXAMPLE=y\n')
        self.assertFalse((self.output / 'sdk').is_symlink())
        self.assertFalse((self.output / 'workspace').exists())
        self.assertFalse((self.output / 'build').exists())
        self.assertTrue((self.output / '.prepare.lock').exists())
        self.assertTrue(prepare(self.root, self.output)[2])

    def test_unchanged_preparation_allows_concurrent_build_consumers(self):
        code = '''from pathlib import Path
import sys
from buildsystem.prepare import execution
with execution(Path(sys.argv[1]), Path(sys.argv[2])):
    print('shared', flush=True)
'''
        environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'scripts')}
        with execution(self.root, self.output):
            result = subprocess.run([sys.executable, '-c', code, str(self.root), str(self.output)],
                                    env=environment, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(result.stdout.strip(), 'shared')

    def test_module_configuration_names_are_independent_and_reach_only_their_build(self):
        host_output = Path(os.environ.get('HYPER_BUILD_OUTPUT', os.environ.get('OUT', ROOT / 'out')))
        executable = backend(ROOT, host_output)
        for name, default in [('alpha', 'y'), ('beta', 'n')]:
            self.describe(name, 'modules/' + name, config={'schema': 'Kconfig'}, build={'steps': [[
                sys.executable, '-c', "import os,sys; from pathlib import Path; Path(sys.argv[1]).write_text(os.environ['CONFIG_DEBUG'])",
                '{out}/observed'] ]})
            (self.root / 'modules' / name / 'Kconfig').write_text(
                f'mainmenu "{name}"\nconfig DEBUG\n    bool "Debug"\n    default {default}\n')
        with patch('buildsystem.config.backend', return_value=executable):
            prepare(self.root, self.output)
        for name, expected in [('alpha', 'y'), ('beta', 'n')]:
            subprocess.run([sys.executable, str(ROOT / 'scripts/build.py'), '--workspace', str(self.root),
                            '--out', str(self.output), '--module', name, 'build'], check=True)
            self.assertEqual((self.output / 'build' / name / 'observed').read_text(), expected)
        alpha = self.output / 'config/aarch64/generated/alpha/config.h'
        beta = self.output / 'config/aarch64/generated/beta/config.h'
        self.assertIn('#define CONFIG_DEBUG 1', alpha.read_text())
        self.assertNotIn('#define CONFIG_DEBUG', beta.read_text())
        self.assertFalse((self.root / 'modules/alpha/.config').exists())


if __name__ == '__main__':
    unittest.main()
