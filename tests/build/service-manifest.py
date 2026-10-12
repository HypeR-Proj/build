#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise host admission of shipped/generated manifests and packaging failures."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from board_config import Board
from board_bootstrap import services

CHECK = ROOT / 'scripts/check-service-manifest.py'
BASE = ROOT / 'bootstrap/stage1/init/config/services-console-only.json'
SPLIT_SPEC = importlib.util.spec_from_file_location(
    'pack_native_system', ROOT / 'scripts/pack-native-system.py')
SPLIT = importlib.util.module_from_spec(SPLIT_SPEC)
SPLIT_SPEC.loader.exec_module(SPLIT)


class ManifestTests(unittest.TestCase):
    def check_manifest(self, path, images=()):
        return subprocess.run([sys.executable, str(CHECK), str(path), *images],
                              capture_output=True, text=True)

    def test_shipped_and_generated(self):
        for path in (ROOT / 'bootstrap/stage1/init').rglob('services*.json'):
            with self.subTest(path=path):
                result = self.check_manifest(path)
                self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            for board in ('qemu', 'rpi5'):
                source = json.loads((ROOT / f'boards/{board}.json').read_text())
                path.write_text(json.dumps(services(Board.parse(source), ROOT)))
                result = self.check_manifest(path)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_storage_graph_can_omit_business_vm_manager(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            for board in ('qemu', 'rpi5'):
                source = json.loads((ROOT / f'boards/{board}.json').read_text())
                graph = services(Board.parse(source), ROOT)
                manager = next(item for item in graph['services'] if item['name'] == 'vm-manager')
                self.assertFalse(manager['critical'])
                graph['services'] = [item for item in graph['services']
                                     if item['name'] != 'vm-manager']
                graph.pop('virtual-machines', None)
                path.write_text(json.dumps(graph))
                result = self.check_manifest(path)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_renamed_vm_manager_stays_outside_the_boot_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            graph = services(Board.load(ROOT / 'boards/qemu.json'), ROOT)
            manager = next(item for item in graph['services'] if item['name'] == 'vm-manager')
            manager['name'] = 'guest-fleet'
            source = directory / 'services.json'
            source.write_text(json.dumps(graph))
            core, system = SPLIT.services(source)
            self.assertNotIn('guest-fleet', [item['name'] for item in core['services']])
            self.assertEqual([item['name'] for item in system['services']], ['session-manager', 'guest-fleet'])
            core_path, system_path = directory / 'core.json', directory / 'system.json'
            core_path.write_text(json.dumps(core))
            system_path.write_text(json.dumps(system))
            result = self.check_manifest(system_path, ['--core', str(core_path)])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_formal_services_are_selected_by_loader_not_vm_role(self):
        graph = json.loads(BASE.read_text())
        service = {'name': 'logger', 'image': '/svc/logger', 'critical': False,
                   'restart': 'never', 'after': ['console-raw'], 'capabilities': []}
        graph['services'].append(service)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'services.json'
            source.write_text(json.dumps(graph))
            core, system = SPLIT.services(source)
            self.assertEqual([item['name'] for item in system['services']], ['session-manager', 'logger'])
            self.assertEqual(system['services'][-1]['image'], '/system/svc/logger')
            self.assertTrue(all(item['loader'] == 'bootstrap' for item in core['services']))
            for item in (graph['services'][0], graph['services'][-1]):
                item['phase'] = 'system' if item.get('loader') == 'bootstrap' else 'boot'
                source.write_text(json.dumps(graph))
                with self.assertRaisesRegex(ValueError, 'phase conflicts'):
                    SPLIT.services(source)
                del item['phase']

    def test_no_business_services_still_produces_a_formal_manifest(self):
        core, system = SPLIT.services(BASE)
        self.assertEqual(len(core['services']), 1)
        self.assertEqual([item['name'] for item in system['services']], ['session-manager'])
        self.assertNotIn('virtual-machines', system)
        self.assertEqual(system['format'], 'hyper.service-manifest')

    def test_composition_rejects_duplicate_source_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            path.write_text(BASE.read_text().replace('"format":',
                            '"format": "duplicate", "format":', 1))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                SPLIT.services(path)

    def test_rejects_invalid_policy_and_syntax(self):
        original = json.loads(BASE.read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            cases = ['{', BASE.read_text().replace('"format":', '"format": "duplicate", "format":', 1)]
            for mutation in ('dependency', 'cycle', 'rights', 'restart', 'loader', 'unknown'):
                value = copy.deepcopy(original)
                service = value['services'][0]
                if mutation == 'dependency':
                    service['after'] = ['missing-service']
                elif mutation == 'cycle':
                    service['after'] = [service['name']]
                elif mutation == 'rights':
                    service['capabilities'][0]['rights'] = ['invented-right']
                elif mutation == 'restart':
                    service['restart'] = 'always'
                elif mutation == 'loader':
                    service['loader'] = 'automatic'
                else:
                    service['typo'] = True
                cases.append(json.dumps(value))
            for text in cases:
                path.write_text(text)
                result = self.check_manifest(path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(path), result.stderr)
            result = self.check_manifest(BASE, ['bin/nonexistent'])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('absent from the archive', result.stderr)

    def test_packer_rejects_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            path.write_text('{')
            output = Path(temporary) / 'initramfs.cpio'
            output.write_bytes(b'previous valid image')
            result = subprocess.run([
                sys.executable, str(ROOT / 'scripts/pack-native-initramfs.py'),
                '--packer', 'does-not-exist', '--strip', 'does-not-exist',
                '--output', str(output), '0644', 'etc/hyper/services.json', str(path),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('service manifest:', result.stderr)
            self.assertEqual(output.read_bytes(), b'previous valid image')


if __name__ == '__main__':
    unittest.main()
