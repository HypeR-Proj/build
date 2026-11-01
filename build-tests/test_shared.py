# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem.shared import configure, identities, verify
from buildsystem.link_context import link_units, validate_lock


class SharedContracts(unittest.TestCase):
    def registry(self):
        return {'library': (None, {'features': ['full'], 'default_features': False},
                            {'package': {'name': 'library'},
                             'features': {'full': ['parse'], 'parse': [], 'experimental': []}})}

    def test_consumer_subset_receives_the_fixed_provider_without_mutating_source(self):
        source = {'dependencies': {'library': {'features': ['parse'], 'default-features': False}}}
        result = configure(source, self.registry(), 'consumer')
        self.assertEqual(result['dependencies']['library']['features'], ['full'])
        self.assertEqual(source['dependencies']['library']['features'], ['parse'])

    def test_unsupported_consumer_feature_is_not_unified_into_the_provider(self):
        with self.assertRaisesRegex(ValueError, 'does not provide features'):
            configure({'dependencies': {'renamed': {'package': 'library', 'features': ['experimental']}}},
                      self.registry(), 'consumer')

    def graph(self):
        def unit(name, kind='lib', dependencies=()):
            return {'pkg_id': name + '@1', 'target': {'name': name, 'crate_types': [kind]},
                    'profile': {'lto': 'fat'}, 'mode': 'build', 'platform': 'native', 'features': [],
                    'dependencies': [{'index': index, 'noprelude': False} for index in dependencies]}
        return {'version': 1, 'roots': [2], 'units': [unit('implementation'), unit('library', 'dylib', [0]),
                                                    unit('program', 'bin', [1])]}

    def test_transitive_version_feature_and_profile_mismatches_are_rejected(self):
        expected = identities(self.graph())
        for field, value in [('pkg_id', 'implementation@2'), ('features', ['extra']), ('profile', {'lto': False})]:
            graph = self.graph()
            graph['units'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'incompatible shared library'):
                verify(expected, identities(graph), 'consumer')

    def test_unit_numbering_does_not_change_compile_identity(self):
        graph = self.graph()
        renumbered = deepcopy(graph)
        renumbered['units'].reverse()
        for unit in renumbered['units']:
            for edge in unit['dependencies']:
                edge['index'] = 2 - edge['index']
        self.assertEqual(identities(graph), identities(renumbered))

    def test_link_context_does_not_inherit_builtin_or_host_dependencies(self):
        graph = self.graph()
        for mode in ('builtin', 'host'):
            excluded = deepcopy(graph['units'][0])
            excluded['pkg_id'] = mode
            if mode == 'host':
                excluded['platform'] = None
            graph['units'].append(excluded)
            graph['units'][1]['dependencies'].append({'index': len(graph['units']) - 1, 'noprelude': mode == 'builtin'})
        self.assertEqual(set(link_units(graph)), {'implementation@1', 'library@1'})

    def test_derived_lock_can_only_add_the_private_anchor(self):
        original = {'package': [{'name': 'dependency', 'version': '1', 'source': 'registry', 'checksum': 'abc'}]}
        result = deepcopy(original)
        result['package'].append({'name': 'anchor', 'version': '0.0.0'})
        validate_lock(original, result, 'anchor')
        result['package'][0]['version'] = '2'
        with self.assertRaisesRegex(ValueError, 'changed a repository-locked dependency'):
            validate_lock(original, result, 'anchor')


if __name__ == '__main__':
    unittest.main()
