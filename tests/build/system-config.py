#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise product selection, build identity and generated startup policy."""

import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import system_config
from native_components import load_registry
from board_config import Board
import board_bootstrap
import component_composition

DEPLOYMENT = ROOT / 'scripts/app-deployment.py'
REGISTRY = ROOT / 'mk/components.mk'
spec = importlib.util.spec_from_file_location('split', ROOT / 'scripts/pack-native-system.py')
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)


class ProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = load_registry(REGISTRY)['entries']

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hyper product test ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def config(self, value, filename='product.json'):
        path = self.root / filename
        path.write_text(json.dumps(value))
        return system_config.load(path)

    def test_default_preserves_boot_environment_and_excludes_acceptance_apps(self):
        selected = self.config({'version': 1}).select(self.entries)
        names = {entry['staged'] for entry in selected if 'binary' in entry}
        self.assertTrue({'init', 'io-runtime', 'fs-fat', 'sh', 'vm-manager', 'editor'} <= names)
        self.assertFalse({'test-report', 'terminal-probe', 'lifecycle-stress'} & names)
        for entry in selected:
            if entry.get('environment') == 'boot' and 'binary' in entry:
                self.assertEqual(entry['link_mode'], 'static')

    def test_fs_workers_are_optional_without_rewriting_mount_policy(self):
        product = self.config({'version': 1, 'boot': {'exclude': ['fs-fat']},
                               'system': {'exclude': ['fs-fat-system']}})
        names = {entry['staged'] for entry in product.select(self.entries) if 'binary' in entry}
        self.assertFalse({'fs-fat', 'fs-fat-system'} & names)
        self.assertTrue({'fs-backend', 'fs-backend-system', 'fs-erofs', 'fs-erofs-system'} <= names)

    def test_minimal_example_selects_exact_formal_executables(self):
        path = ROOT / 'configs/minimal.json'
        product = system_config.load(path)
        selected = product.select(self.entries)
        formal = {entry['staged'] for entry in selected
                  if 'binary' in entry and entry.get('environment') == 'system'}
        self.assertEqual(formal, set(json.loads(path.read_text())['system']['include']))
        self.assertFalse({'fs-fat-system', 'vm-manager', 'vm-runtime', 'vmm', 'editor'} & formal)
        self.assertNotIn('fs-fat', product.boot_components(self.entries))

    def test_allowlist_does_not_grow_when_a_component_is_added(self):
        product = self.config({'version': 1, 'system': {'base': 'none', 'include': ['echo']}})
        later = self.entries + [{'binary': 'hyper-later', 'staged': 'later',
                                 'environment': 'system', 'profiles': ['system']}]
        formal = [entry['staged'] for entry in product.select(later)
                  if 'binary' in entry and entry.get('environment') == 'system']
        self.assertEqual(formal, ['echo'])

    def test_excluding_required_child_fails_instead_of_adding_it_back(self):
        for parent, child in [('session-manager', 'terminal-service'),
                              ('session-manager', 'sh'), ('vm-manager', 'vm-runtime'),
                              ('component-manager', 'svc/journal')]:
            with self.subTest(parent=parent, child=child):
                product = self.config({'version': 1, 'system': {'exclude': [child]}})
                with self.assertRaisesRegex(ValueError, f'{parent}.*{child}'):
                    product.select(self.entries)

    def test_missing_and_wrong_environment_names_are_errors(self):
        for environment, names in [('boot', ['does-not-exist']), ('system', ['init']),
                                   ('boot', ['fs-fat-system']), ('system', ['fs-fat'])]:
            with self.subTest(environment=environment, names=names):
                product = self.config({'version': 1, environment: {'exclude': names}})
                with self.assertRaisesRegex(ValueError, f'unknown {environment} components'):
                    product.select(self.entries)

    def test_malformed_configuration_is_rejected(self):
        invalid = [[], {'version': True}, {'version': 2}, {'version': 1, 'typo': {}},
                   {'version': 1, 'boot': {'base': 'all'}},
                   {'version': 1, 'system': {'includes': ['ls']}},
                   {'version': 1, 'system': {'include': ['ls', 'ls']}},
                   {'version': 1, 'system': {'include': ['ls'], 'exclude': ['ls']}},
                   {'version': 1, 'system': {'include': ['../ls']}},
                   {'version': 1, 'kernel': {'features': ['rtc', 'rtc']}},
                   {'version': 1, 'kernel': {'features': 'rtc'}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config(value)
        path = self.root / 'duplicate.json'
        path.write_text('{"version":1,"system":{"base":"none","base":"default"}}')
        with self.assertRaisesRegex(ValueError, 'duplicate product configuration key'):
            system_config.load(path)

    def test_identity_uses_contents_and_normalizes_order(self):
        original = {'version': 1, 'system': {'exclude': ['editor', 'less']},
                    'kernel': {'features': ['rtc', 'runtime-symbols']}}
        first = self.config(original).identity()
        reordered = {'SPDX-License-Identifier': 'Apache-2.0',
                     'kernel': {'features': ['runtime-symbols', 'rtc']}, 'version': 1,
                     'system': {'exclude': ['less', 'editor']}}
        self.assertEqual(first, self.config(reordered, 'different-name.json').identity())
        original['system']['exclude'].append('grep')
        self.assertNotEqual(first, self.config(original).identity())
        self.assertNotEqual(self.config({'version': 1}).identity(),
                            self.config({'version': 1, 'kernel': {'features': []}}).identity())

    def test_cli_selection_agrees_with_manifest_and_omits_unselected_build_targets(self):
        path = ROOT / 'configs/minimal.json'
        def invoke(action, *extra):
            return subprocess.check_output(
                [sys.executable, str(DEPLOYMENT), action, '--manifest', str(REGISTRY),
                 '--system-config', str(path), '--profile', 'system', *extra],
                cwd=ROOT, text=True)
        selected = json.loads(invoke('manifest'))['entries']
        targets = invoke('cargo-targets', '--link-mode', 'dynamic', '--executable-roots').split()
        binaries = {targets[index + 1] for index, value in enumerate(targets[:-1]) if value == '--bin'}
        expected = {entry['binary'] for entry in selected
                    if 'binary' in entry and entry.get('link_mode') == 'dynamic'}
        self.assertEqual(binaries, expected)
        self.assertNotIn('hyper-fs-fat-system', binaries)
        self.assertNotIn('hyper-vm-manager', binaries)
        self.assertNotIn('hyper-editor', binaries)
        self.assertIn('hyper-shell', binaries)
        packages = {targets[index + 1] for index, value in enumerate(targets[:-1]) if value == '--package'}
        library_packages = {entry['package'] for entry in self.entries if 'library' in entry}
        self.assertFalse(packages & library_packages)

    def test_omitting_services_removes_startup_work_and_unavailable_grants(self):
        source = board_bootstrap.services(Board.load(ROOT / 'boards/qemu.json'), ROOT)
        services = self.root / 'services.json'
        services.write_text(json.dumps(source))
        for headless in (False, True):
            with self.subTest(headless=headless):
                excluded = ['vm-manager', 'vm-runtime', 'vmm']
                if headless:
                    excluded.extend(['session-manager', 'terminal-service', 'sh'])
                product = self.config({'version': 1, 'system': {'exclude': excluded}})
                selected = product.select(self.entries)
                images = {'/' + entry['destination'] for entry in selected
                          if 'binary' in entry and entry.get('environment') == 'system'}
                core, formal = split.services(services, images)
                self.assertNotIn('virtual-machines', formal)
                declarations, policy = component_composition.compose(core, formal, mounts=False)
                self.assertNotIn('vm-manager', declarations)
                self.assertNotIn('mounts', declarations)
                self.assertNotIn('mounts', policy)
                self.assertFalse(any('fleet' in app for app in policy['applications']))
                for app in policy['applications']:
                    self.assertFalse(any(grant['purpose'] == 'vm.manager-connection'
                                         for grant in app['capabilities']))
                self.assertEqual('session-manager' in declarations, not headless)
                if headless:
                    self.assertFalse(any(app.get('interactive') for app in policy['applications']))

    def test_product_kernel_section_cannot_enable_test_hooks(self):
        for name in ('kernel-test', 'io-dma-revoke-test', 'kernel-self-test', 'missing'):
            with self.subTest(name=name):
                path = self.root / 'product.json'
                path.write_text(json.dumps({'version': 1, 'kernel': {'features': [name]}}))
                result = subprocess.run(
                    [sys.executable, str(DEPLOYMENT), 'kernel-features',
                     '--manifest', str(REGISTRY), '--system-config', str(path)],
                    cwd=ROOT, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_system_only_board_keeps_a_usable_empty_fleet_configuration(self):
        board = Board.load(ROOT / 'boards/qemu-minimal.json')
        board_output = self.root / 'board'
        board_bootstrap.stage(board, ROOT, board_output)
        apps, boot, sdk = [self.root / name for name in ('apps', 'boot-apps', 'sdk')]
        roots = {'apps': str(apps), 'sdk': str(sdk), 'arch': 'aarch64'}
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', elf, 18, 183)
        struct.pack_into('<H', elf, 54, 56)
        self.config({'version': 1})
        for entry in self.entries:
            if ('library' in entry or 'symlink' in entry
                    or (entry.get('environment') != 'boot' and 'system' not in entry['profiles'])):
                continue
            directory = boot if entry.get('environment') == 'boot' else apps
            path = (Path(entry['source'].format_map(roots)) if 'source' in entry
                    else directory / entry['staged'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(elf)
        manifest = self.root / 'registry.json'
        manifest.write_text(json.dumps({'version': 1, 'entries': self.entries}))
        output = self.root / 'output'
        output.mkdir()
        (output / 'io.entries.json').write_text('[]')
        arguments = ['pack-native-system', '--board', str(ROOT / 'boards/qemu-minimal.json'),
                     '--board-output', str(board_output), '--deployment', str(manifest),
                     '--system-config', str(self.root / 'product.json'), '--apps', str(apps),
                     '--boot-apps', str(boot), '--sdk', str(sdk), '--std', str(self.root / 'std'),
                     '--output', str(output), '--arch', 'aarch64', '--packer', 'unused-packer',
                     '--strip', 'unused-strip', '--boot-components', *system_config.DEFAULT_BOOT]
        # Run real board composition and staging; only external ELF stripping,
        # manifest executables and cpio creation are outside this Python test.
        with patch.object(sys, 'argv', arguments), patch.object(split.subprocess, 'run'):
            split.main()
        startup = json.loads((output / 'system-root/etc/hyper/startup.json').read_text())
        manager = next(app for app in startup['applications'] if app['component'] == '/vm-manager')
        self.assertEqual(manager['fleet'], '/etc/hyper/vms.json')
        fleet = output / 'system-root' / manager['fleet'].lstrip('/')
        self.assertEqual(json.loads(fleet.read_text()),
                         {'format': 'hyper.vm-config', 'virtual-machines': []})
        boot_entries = json.loads((output / 'native-system/boot.entries.json').read_text())
        self.assertNotIn('etc/hyper/vms.json', {name for _, name, _ in boot_entries})


if __name__ == '__main__':
    unittest.main()
