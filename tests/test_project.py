# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import project
import toml_output


class AssemblyTests(unittest.TestCase):
    def test_source_paths_stay_inside_components(self):
        for path in ('../kernel', '/tmp/source', 'x/../../outside', '.git/config'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                project.relative(path)

    def test_cargo_round_trip(self):
        value = {'package': {'name': 'test', 'edition': '2024'},
                 'target': {'cfg(target_os = "hyper")': {'dependencies': {
                     'dependency': {'path': '../abi', 'features': ['std'], 'default-features': False}}}},
                 'bin': [{'name': 'tool', 'path': 'src/main.rs'}]}
        self.assertEqual(tomllib.loads(toml_output.dumps(value)), value)

    def test_current_dependency_features_and_workspace_changes_survive(self):
        specification = {'workspace': None, 'membership': '..', 'dependency_paths': {},
                         'inherited_dependencies': {'/dependencies/abi': {'workspace': True}}}
        source = b'[package]\nname="x"\n[dependencies.abi]\nversion="=0.0.0"\nfeatures=["new"]\n'
        value = tomllib.loads(project.cargo_input(source, 'stdlib/rust/x/Cargo.toml', specification).decode())
        self.assertEqual(value['dependencies']['abi'], {'workspace': True, 'features': ['new']})
        specification = {'workspace': {}, 'membership': None, 'dependency_paths': {},
                         'extra_workspace_members': ['filesystem'],
                         'workspace_dependency_paths': {'abi': 'sdk/abi'}}
        source = b'[workspace]\nmembers=["new-member"]\n[workspace.dependencies]\nabi="=1.2.3"\n'
        value = tomllib.loads(project.cargo_input(source, 'stdlib/rust/Cargo.toml', specification).decode())
        self.assertEqual(value['workspace']['members'], ['new-member', 'filesystem'])
        self.assertEqual(value['workspace']['dependencies']['abi'], {'version': '=1.2.3', 'path': '../../sdk/abi'})

    def test_removed_dependency_is_not_resurrected(self):
        specification = {'workspace': None, 'membership': None,
                         'dependency_paths': {'/dependencies/removed': 'lib/removed'}}
        value = tomllib.loads(project.cargo_input(b'[package]\nname="x"\n', 'app/x/Cargo.toml', specification).decode())
        self.assertNotIn('dependencies', value)

    def test_unchanged_files_keep_mtime_but_mode_changes_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'script'
            project.atomic_write(path, b'content', 0o644)
            modified = path.stat().st_mtime_ns
            project.atomic_write(path, b'content', 0o644)
            self.assertEqual(path.stat().st_mtime_ns, modified)
            project.atomic_write(path, b'content', 0o755)
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_existing_unowned_directory_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            output.mkdir()
            (output / 'user-file').write_text('keep')
            with self.assertRaisesRegex(ValueError, 'unowned'):
                project.prepare(root / 'sources', output)
            self.assertEqual((output / 'user-file').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
