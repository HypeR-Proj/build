# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem.components import load


class Components(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repositories = {name: self.root / name for name in ('build', 'app', 'tool')}
        self.write('build', 'component.json', name='build', include=[{'project': 'app'}])
        self.write('app', 'component.json', name='app', include=['groups/tools.json'])
        self.write('app', 'groups/tools.json', include=[{'project': 'tool'}])
        self.write('tool', 'component.json', name='tool', build={'environment': {'CONFIG_LOCAL': 'tool'}})

    def write(self, owner, path, **data):
        path = self.repositories[owner] / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': 1, **data}))

    def test_recursive_catalogs_preserve_ownership_and_deduplicate_includes(self):
        self.write('build', 'component.json', name='build', include=[{'project': 'app'}, {'project': 'tool'}])
        modules, inputs = load(self.repositories)
        self.assertEqual(set(modules), {'build', 'app', 'tool'})
        self.assertNotIn('build', modules['app'])
        self.assertEqual(modules['tool']['build']['environment'], {'CONFIG_LOCAL': 'tool'})
        self.assertEqual(len(inputs['tool']), 1)

    def test_component_cycle_has_an_include_chain(self):
        self.write('tool', 'component.json', name='tool', include=[{'project': 'build'}])
        with self.assertRaisesRegex(ValueError, 'cyclic component include: build:component.json'):
            load(self.repositories)

    def test_local_fragments_cannot_override_recipes(self):
        self.write('tool', 'component.json', name='tool', include=['extra.json'], build={'type': 'native-rust'})
        self.write('tool', 'extra.json', build={'type': 'group'})
        with self.assertRaisesRegex(ValueError, 'duplicate definition'):
            load(self.repositories)

    def test_missing_or_escaping_includes_are_errors(self):
        for include, message in [('missing.json', 'missing component include'), ('../outside.json', 'invalid component path'),
                                 ({'project': 'absent'}, 'unknown manifest project')]:
            with self.subTest(include=include):
                self.write('tool', 'component.json', name='tool', include=[include])
                with self.assertRaisesRegex(ValueError, message):
                    load(self.repositories)

    def test_nested_repositories_require_project_includes(self):
        self.repositories['tool'] = self.repositories['app'] / 'tool'
        self.write('tool', 'component.json', name='tool')
        self.write('app', 'component.json', name='app', include=['tool/component.json'])
        with self.assertRaisesRegex(ValueError, 'crosses repository ownership'):
            load(self.repositories)

    def test_obsolete_or_misspelled_fields_are_not_silently_ignored(self):
        for field in ['origin', 'packages', 'incldue']:
            with self.subTest(field=field):
                self.write('tool', 'component.json', name='tool', **{field: {}})
                with self.assertRaisesRegex(ValueError, 'unknown component fields'):
                    load(self.repositories)

    def test_shared_library_requires_a_complete_feature_contract(self):
        self.write('tool', 'component.json', name='tool', cargo=[{'manifest': 'Cargo.toml', 'domain': 'system', 'shared': True}])
        with self.assertRaisesRegex(ValueError, 'must fix features'):
            load(self.repositories)


if __name__ == '__main__':
    unittest.main()
