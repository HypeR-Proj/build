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
from buildsystem.model import dependency_order, discover, find_workspace
from buildsystem.prepare import cached, execution, generate, prepare


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

    def test_current_repo_wrapper_and_selected_nested_manifest_round_trip(self):
        checkout = self.root / '.repo/manifests'
        (checkout / 'profiles').mkdir()
        (checkout / 'profiles/selected.xml').write_text('<manifest><include name="projects.xml"/></manifest>')
        (checkout / 'projects.xml').write_bytes((checkout / 'default.xml').read_bytes())
        (checkout / 'default.xml').write_text('<manifest><project name="absent"/></manifest>')
        public = self.root / '.repo/manifest.xml'
        public.unlink()
        public.write_text('<manifest><include name="profiles/selected.xml"/></manifest>')
        view, modules, _, _ = prepare(self.root, self.output)
        self.assertEqual(set(modules), {'build', 'alpha', 'beta', 'manifest'})
        self.assertEqual(modules['manifest'].source, checkout)
        self.assertEqual(set(discover(view)), set(modules))
        sys.path.insert(0, str(ROOT))
        import project
        import tarfile
        archive = self.root.parent / 'source.tar.xz'
        project.archive(self.root, archive, self.output)
        extracted = self.root.parent / 'extracted'
        with tarfile.open(archive) as stream:
            stream.extractall(extracted, filter='data')
        restored = extracted / 'HypeR'
        self.assertEqual(set(discover(restored)), set(modules))
        self.assertEqual(project.inventory(restored, self.output)['manifest'], 'manifest/profiles/selected.xml')

    def test_manifest_include_cycles_are_rejected(self):
        checkout = self.root / '.repo/manifests'
        (checkout / 'default.xml').write_text('<manifest><include name="default.xml"/></manifest>')
        with self.assertRaisesRegex(ValueError, 'cyclic manifest include'):
            discover(self.root)

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
        (other / 'component.json').write_bytes((self.root / 'component.json').read_bytes())
        (other / 'modules').symlink_to(self.root / 'modules')
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

    def test_launch_ignores_compiler_inputs_without_weakening_build_freshness(self):
        source = self.root / 'modules/alpha/source.rs'
        source.write_text('before')
        view, _, _, _ = prepare(self.root, self.output)
        source.write_text('after')
        with patch('buildsystem.prepare.collect', side_effect=AssertionError('launch scanned compiler inputs')):
            with execution(self.root, self.output, launch=True) as (ready, _, changed):
                self.assertEqual(ready, view)
                self.assertFalse(changed)
        # A later build must still record the changed compiler input.
        self.assertIsNone(cached(self.root, self.output, 'aarch64'))
        with execution(self.root, self.output) as (_, _, changed):
            self.assertTrue(changed)

    def test_launch_refreshes_timestamp_preserving_control_changes_and_repairs_view(self):
        view, _, _, _ = prepare(self.root, self.output)
        source = self.root / 'policy.py'
        previous = source.stat()
        source.write_text('modified policy\n')
        os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        with execution(self.root, self.output, launch=True) as (_, _, changed):
            self.assertTrue(changed)
        self.assertEqual((view / 'policy.py').read_bytes(), source.read_bytes())
        for corrupt in ('body', 'mode', 'link'):
            with self.subTest(corrupt=corrupt):
                path = view / 'policy.py'
                if corrupt == 'body':
                    path.write_text('corrupt generated script')
                elif corrupt == 'mode':
                    path.chmod(0o700)
                else:
                    path.unlink()
                    path.symlink_to(source)
                with execution(self.root, self.output, launch=True) as (_, _, changed):
                    self.assertTrue(changed)
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), source.read_bytes())
                self.assertEqual(path.stat().st_mode & 0o777, source.stat().st_mode & 0o777)

    def test_launch_detects_new_helpers_in_previously_source_only_directories(self):
        directory = self.root / 'modules/alpha/nested'
        directory.mkdir()
        (directory / 'source.rs').write_text('source')
        view, _, _, _ = prepare(self.root, self.output)
        helper = directory / 'helper.py'
        helper.write_text('new helper')
        with execution(self.root, self.output, launch=True) as (_, _, changed):
            self.assertTrue(changed)
        mirrored = view / 'modules/alpha/nested/helper.py'
        self.assertEqual(mirrored.read_bytes(), helper.read_bytes())
        helper.unlink()
        with execution(self.root, self.output, launch=True) as (_, _, changed):
            self.assertTrue(changed)
        self.assertFalse(mirrored.exists())

    def test_launch_revalidates_saved_configs_metadata_architecture_and_owner(self):
        configuration = self.output / 'config/aarch64/local.config'
        configuration.parent.mkdir(parents=True)
        configuration.write_text('before')
        prepare(self.root, self.output)
        self.assertIsNotNone(cached(self.root, self.output, 'aarch64', launch=True))
        configuration.write_text('after')
        self.assertIsNone(cached(self.root, self.output, 'aarch64', launch=True))
        configuration.write_text('before')
        self.assertIsNone(cached(self.root, self.output, 'riscv64', launch=True))
        self.describe('alpha', 'modules/alpha', documentation={'title': 'changed metadata'})
        self.assertIsNone(cached(self.root, self.output, 'aarch64', launch=True))
        prepare(self.root, self.output)
        (self.output / 'workspace-owner.json').write_text('{"workspace":"another","version":1}')
        with self.assertRaisesRegex(ValueError, 'another workspace'):
            with execution(self.root, self.output, launch=True):
                self.fail('launch accepted foreign output')

    def test_launch_accepts_source_symlinks_but_refreshes_their_contents(self):
        source = self.root / 'policy.py'
        alias = self.root / 'alias.py'
        alias.symlink_to(source)
        view, _, _, _ = prepare(self.root, self.output)
        self.assertIsNotNone(cached(self.root, self.output, 'aarch64', launch=True))
        source.write_text('updated target')
        with execution(self.root, self.output, launch=True):
            self.assertEqual((view / 'alias.py').read_bytes(), source.read_bytes())

    def test_launch_detects_helpers_added_to_empty_directories(self):
        directory = self.root / 'modules/alpha/empty/nested'
        directory.mkdir(parents=True)
        view, _, _, _ = prepare(self.root, self.output)
        (directory / 'helper.py').write_text('new helper')
        with execution(self.root, self.output, launch=True):
            self.assertEqual((view / 'modules/alpha/empty/nested/helper.py').read_text(), 'new helper')

    def test_launch_does_not_cache_an_addition_racing_with_generation(self):
        def add_helper(*args):
            (self.root / 'modules/alpha/helper.py').write_text('new helper')
            return generate(*args)

        with patch('buildsystem.prepare.generate', side_effect=add_helper):
            view, _, _, _ = prepare(self.root, self.output)
        self.assertFalse((view / 'modules/alpha/helper.py').exists())
        self.assertIsNone(cached(self.root, self.output, 'aarch64', launch=True))
        with execution(self.root, self.output, launch=True):
            self.assertEqual((view / 'modules/alpha/helper.py').read_text(), 'new helper')

    def test_launch_revalidates_link_changes_in_included_manifests(self):
        (self.root / 'modules/alpha/data.txt').write_text('data')
        manifest = self.root / '.repo/manifests/default.xml'
        included = manifest.with_name('projects.xml')
        included.write_text(manifest.read_text().replace(
            '<project name="alpha" path="modules/alpha"/>',
            '<project name="alpha" path="modules/alpha">'
            '<linkfile src="data.txt" dest="first.txt"/></project>'))
        manifest.write_text('<manifest><include name="projects.xml"/></manifest>')
        view, _, _, _ = prepare(self.root, self.output)
        included.write_text(included.read_text().replace('first.txt', 'other.txt'))
        self.assertIsNone(cached(self.root, self.output, 'aarch64', launch=True))
        with execution(self.root, self.output, launch=True):
            self.assertFalse((view / 'first.txt').exists())
            self.assertEqual((view / 'other.txt').read_text(), 'data')

    def test_module_configuration_names_are_independent_and_reach_only_their_build(self):
        workspace = find_workspace(ROOT)
        host_output = Path(os.environ.get('HYPER_BUILD_OUTPUT', os.environ.get('OUT', workspace / 'out')))
        executable = backend(workspace, host_output)
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
