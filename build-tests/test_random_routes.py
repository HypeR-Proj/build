# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Random client grants require a provider, and never grant provider READ."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from component_composition import compose
from system_config import ProductConfig
from boot_profiles import storage_providers


class RandomRoutes(unittest.TestCase):
    def test_default_io_vm_declares_random_and_explicit_override_is_preserved(self):
        profiles = {'default': {'io': {'kind': 'io-vm'}, 'handoff': {}}}
        self.assertEqual(storage_providers(ProductConfig({'version': 1}), profiles), [
            {'component': '/io-runtime', 'image': '/svc/io-runtime', 'random': True}
        ])
        explicit = ProductConfig({'version': 1, 'system': {'storage-providers': []}})
        self.assertEqual(storage_providers(explicit, profiles), [])
        self.assertEqual(storage_providers(ProductConfig({'version': 1}),
                                           {'default': {'io': {'kind': 'none'}}}), [])

    def test_product_random_declaration_is_strictly_boolean(self):
        for invalid in (0, 1, 'true', None, []):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                ProductConfig({'version': 1, 'system': {'storage-providers': [
                    {'component': '/owner', 'image': '/svc/owner', 'random': invalid}
                ]}})
        for accepted in (False, True):
            config = ProductConfig({'version': 1, 'system': {'storage-providers': [
                {'component': '/owner', 'image': '/svc/owner', 'random': accepted}
            ]}})
            self.assertIs(config.value['system']['storage-providers'][0]['random'], accepted)

    def policy(self, providers):
        return compose({'services': []}, {'services': [
            {'name': 'session-manager', 'image': '/svc/session-manager'},
            {'name': 'ordinary-tool', 'image': '/bin/tool'},
        ]}, storage_providers=providers)

    def test_no_io_vm_does_not_inject_random_client(self):
        for providers in ([], [{'component': '/block-driver', 'image': '/svc/custom-block'}],
                          [{'component': '/io-runtime', 'image': '/svc/io-runtime'}]):
            with self.subTest(providers=providers):
                _, policy = self.policy(providers)
                for app in policy['applications']:
                    self.assertFalse(any(g['purpose'] == 'random.client' for g in app['capabilities']))

    def test_io_vm_grants_attenuated_client_to_session_and_ordinary_apps(self):
        units, policy = self.policy([{'component': '/io-runtime', 'image': '/svc/io-runtime',
                                      'random': True}])
        self.assertTrue(next(p for p in policy['providers'] if p['role'] == 'storage')['random'])
        self.assertEqual(len(policy['applications']), 2)
        for app in policy['applications']:
            grants = [g for g in app['capabilities'] if g['purpose'] == 'random.client']
            self.assertEqual(grants, [{'source': 'random.client', 'purpose': 'random.client',
                                      'operation': 'duplicate',
                                      'rights': ['write', 'wait', 'duplicate', 'transfer']}])
        self.assertFalse(any(a['component'] == '/io-runtime' for a in policy['applications']))
        self.assertNotIn('capabilities', units['io-runtime'])

    def test_random_support_is_a_provider_declaration_not_an_image_name(self):
        _, policy = self.policy([
            {'component': '/block-driver', 'image': '/svc/custom-block'},
            {'component': '/entropy-capable', 'image': '/svc/custom-owner', 'random': True},
        ])
        for app in policy['applications']:
            self.assertTrue(any(g['purpose'] == 'random.client' for g in app['capabilities']))


if __name__ == '__main__':
    unittest.main()
