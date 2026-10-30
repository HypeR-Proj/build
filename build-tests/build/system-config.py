#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise product selection, build identity and generated startup policy."""

import importlib.util
import json
import os
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
from product_fixture import trimmed_product, write_product

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

    def config(self, value, filename='product.config'):
        path = self.root / filename
        write_product(path, value)
        return system_config.load(path, self.entries)

    def test_default_preserves_boot_environment_and_excludes_acceptance_apps(self):
        selected = self.config({'version': 1}).select(self.entries)
        names = {entry['staged'] for entry in selected if 'binary' in entry}
        self.assertTrue({'init', 'io-runtime', 'fs-fat', 'sh', 'vm-manager', 'editor'} <= names)
        self.assertFalse({'test-report', 'terminal-probe', 'lifecycle-stress'} & names)
        for entry in selected:
            if entry.get('environment') == 'boot' and 'binary' in entry:
                self.assertEqual(entry['link_mode'], 'static')

    def test_saved_product_does_not_implicitly_enable_development_programs(self):
        self.config({'version': 1})
        command = [sys.executable, str(DEPLOYMENT), 'manifest', '--manifest', str(REGISTRY)]
        ordinary = json.loads(subprocess.check_output(command + ['--profile', 'system'], text=True))
        for profile in ('system', 'development', 'io'):
            saved = json.loads(subprocess.check_output(command + [
                '--profile', profile, '--system-config', str(self.root / 'product.config')], text=True))
            programs = lambda entries: {entry['staged'] for entry in entries['entries'] if 'binary' in entry}
            self.assertEqual(programs(ordinary), programs(saved))
            self.assertNotIn('test-report', programs(saved))

    def test_fs_workers_are_optional_without_rewriting_mount_policy(self):
        product = self.config({'version': 1, 'boot': {'exclude': ['fs-fat']},
                               'system': {'exclude': ['fs-fat-system']}})
        names = {entry['staged'] for entry in product.select(self.entries) if 'binary' in entry}
        self.assertFalse({'fs-fat', 'fs-fat-system'} & names)
        self.assertTrue({'fs-backend', 'fs-backend-system', 'fs-erofs', 'fs-erofs-system'} <= names)

    def test_defaults_follow_the_domain_kconfig_definitions(self):
        import kconfig
        product = self.config({'version': 1})
        symbols = system_config.component_symbols(self.entries)
        for scope in ('boot', 'system'):
            expected = {name for symbol, (environment, name) in symbols.items()
                        if environment == scope and kconfig.schema()[symbol]['default'] == 'y'}
            self.assertEqual(product.selected_names(self.entries, scope), expected)

    def test_trimmed_fixture_selects_exact_formal_executables(self):
        source = trimmed_product(self.entries)
        product = self.config(source)
        selected = product.select(self.entries)
        formal = {entry['staged'] for entry in selected
                  if 'binary' in entry and entry.get('environment') == 'system'}
        self.assertEqual(formal, set(source['system']['include']))
        self.assertFalse({'fs-fat-system', 'vm-manager', 'vm-runtime', 'vmm', 'editor'} & formal)
        self.assertNotIn('fs-fat', product.boot_components(self.entries))

    def test_allowlist_does_not_grow_when_a_component_is_added(self):
        product = self.config({'version': 1, 'system': {'base': 'none', 'include': ['echo']}})
        later = self.entries + [{'binary': 'hyper-later', 'staged': 'later',
                                 'environment': 'system', 'profiles': ['system']}]
        formal = [entry['staged'] for entry in product.select(later)
                  if 'binary' in entry and entry.get('environment') == 'system']
        self.assertEqual(formal, ['echo'])

    def test_required_program_graph_is_checked_for_every_edge(self):
        # Check all declarations, not just the service pairs that prompted a fix.
        for entry in self.entries:
            for dependency in entry.get('runtime_requires', []):
                with self.subTest(program=entry['staged'], prerequisite=dependency):
                    product = system_config.ProductConfig({'version': 1, entry['environment']: {
                        'include': [entry['staged']], 'exclude': [dependency]}})
                    with self.assertRaisesRegex(ValueError, 'requires deployed component'):
                        product.select(self.entries, profile='development')

    def test_explicit_missing_prerequisites_fail_in_kconfig(self):
        for parent, child in [('SESSION_MANAGER', 'TERMINAL_SERVICE'),
                              ('SESSION_MANAGER', 'SH'), ('VM_MANAGER', 'VM_RUNTIME'),
                              ('FS_FAT_SYSTEM', 'FS_BACKEND_SYSTEM'),
                              ('EDITOR', 'TERMINAL_SERVICE'), ('VMM', 'VM_MANAGER'),
                              ('MOUNT_SYSTEM', 'FS_BACKEND_SYSTEM')]:
            with self.subTest(parent=parent, child=child):
                path = self.root / 'broken.config'
                path.write_text(f'CONFIG_SYSTEM_APP_{parent}=y\n# CONFIG_SYSTEM_APP_{child} is not set\n')
                with self.assertRaisesRegex(ValueError, f'CONFIG_SYSTEM_APP_{parent}.*requires'):
                    system_config.load(path)

    def test_headless_and_storage_selection_preserve_unrelated_tools(self):
        core = {'component-manager', 'system-startup', 'svc/bootstrap-retire',
                'console-raw-system', 'svc/journal'}
        path = self.root / 'headless.config'
        path.write_text('# CONFIG_SYSTEM_APP_SESSION_MANAGER is not set\n'
                        '# CONFIG_SYSTEM_APP_TERMINAL_SERVICE is not set\n'
                        '# CONFIG_SYSTEM_APP_SH is not set\n'
                        '# CONFIG_SYSTEM_APP_FS_BACKEND_SYSTEM is not set\n'
                        '# CONFIG_SYSTEM_APP_IO_RUNTIME_SYSTEM is not set\n'
                        '# CONFIG_SYSTEM_APP_VM_MANAGER is not set\n')
        product = system_config.load(path)
        names = {entry['staged'] for entry in product.select(self.entries) if 'binary' in entry}
        self.assertTrue(core <= names)
        self.assertFalse({'editor', 'vmm', 'mount-system', 'fs-fat-system'} & names)
        self.assertTrue({'ls', 'cat', 'less'} <= names)
        self.assertIn('io-runtime', names)  # Formal selection never trims stage1.

    def test_missing_and_wrong_environment_names_are_errors(self):
        for environment, names in [('boot', ['does-not-exist']), ('system', ['init']),
                                   ('boot', ['fs-fat-system']), ('system', ['fs-fat'])]:
            with self.subTest(environment=environment, names=names):
                product = system_config.ProductConfig({'version': 1, environment: {'exclude': names}})
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
        path.write_text('CONFIG_SYSTEM_APP_EDITOR=y\n# CONFIG_SYSTEM_APP_EDITOR is not set\n')
        with self.assertRaisesRegex(ValueError, 'duplicate assignment'):
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
        self.config(trimmed_product(self.entries))
        path = self.root / 'product.config'
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

    def test_handoff_checks_live_providers_not_only_packaged_programs(self):
        source = board_bootstrap.services(Board.load(ROOT / 'boards/qemu.json'), ROOT)
        path = self.root / 'services.json'
        path.write_text(json.dumps(source))
        core, formal = split.services(path)
        _, policy = component_composition.compose(core, formal)
        profile = {'handoff': {'system-init': '/svc/component-manager'}}
        def validate(manifest):
            split.validate_formal_sources([('default', profile, path, manifest)], policy)
        validate(core)
        for purpose in ('console.system', 'console.logs', 'console.control',
                        'filesystem.control-server', 'io.broker-server'):
            with self.subTest(purpose=purpose):
                manifest = json.loads(json.dumps(core))
                for service in manifest['services']:
                    service['capabilities'] = [g for g in service.get('capabilities', [])
                                               if g['purpose'] != purpose]
                with self.assertRaisesRegex(ValueError, 'boot profile default'):
                    validate(manifest)

    def test_defconfig_round_trip_migration_and_failure_are_atomic(self):
        fragment = self.root / 'custom_defconfig'
        fragment.write_text('# CONFIG_SYSTEM_APP_VM_MANAGER is not set\n'
                            '# CONFIG_BOOT_APP_FS_FAT is not set\n')
        full, minimal = self.root / '.config', self.root / 'defconfig'
        kernel = self.root / 'kernel.config'
        def make(command, **settings):
            return subprocess.run(['make', '--no-print-directory', command,
                                   'ARCH=aarch64', f'CONFIG_FILE={kernel}',
                                   *[f'{key}={value}' for key, value in settings.items()]],
                                  cwd=ROOT, text=True, capture_output=True)
        generated = make('defconfig', SYSTEM_CONFIG=full, SYSTEM_DEFCONFIG=fragment)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        before_kernel = kernel.read_bytes()
        self.assertIn(b'CONFIG_ARCH_AARCH64=y', before_kernel)
        product = system_config.load(full)
        self.assertNotIn('vmm', product.selected_names(self.entries, 'system'))
        saved = make('savedefconfig', SYSTEM_CONFIG=full, SYSTEM_DEFCONFIG_OUTPUT=minimal)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(system_config.load(minimal).identity(), product.identity())
        full.write_text(full.read_text() + 'CONFIG_REMOVED_APP=y\n')
        migrated = make('olddefconfig', SYSTEM_CONFIG=full)
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.assertIn('CONFIG_REMOVED_APP', migrated.stderr)
        self.assertEqual(system_config.load(full).identity(), product.identity())
        original = full.read_bytes()
        fragment.write_text('CONFIG_SYSTEM_APP_VM_MANAGER=y\n# CONFIG_SYSTEM_APP_VM_RUNTIME is not set\n')
        rejected = make('defconfig', SYSTEM_CONFIG=full, SYSTEM_DEFCONFIG=fragment)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('CONFIG_SYSTEM_APP_VM_MANAGER', rejected.stderr)
        self.assertEqual(full.read_bytes(), original)
        self.assertEqual(kernel.read_bytes(), before_kernel)

    def test_top_level_defconfig_resets_both_configs_for_the_selected_architecture(self):
        kernel = self.root / 'kernel settings.config'
        product = self.root / 'product.config'
        expected_product = self.config({'version': 1}, filename='expected-product.config')
        for arch in ('aarch64', 'riscv64', 'x86_64'):
            with self.subTest(arch=arch):
                kernel.write_text('CONFIG_ARCH_AARCH64=y\nCONFIG_MAX_CPUS=23\n'
                                  'CONFIG_FEATURE_KERNEL_TEST=y\n')
                # Root-relative paths must retain their meaning after make -C.
                relative = os.path.relpath(kernel, ROOT)
                result = subprocess.run(
                    ['make', '--no-print-directory', 'defconfig', f'ARCH={arch}',
                     f'SYSTEM_CONFIG={product}', f'CONFIG_FILE={relative}'],
                    cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                schema = ROOT / 'kernel/Kconfig'
                expected = system_config.kconfig.query(
                    'resolve', ROOT / f'kernel/configs/qemu_{arch}_defconfig', schema=schema)
                actual = system_config.kconfig.query('resolve', kernel, schema=schema)
                self.assertEqual(actual['values'], expected['values'])
                self.assertEqual(system_config.load(product).identity(),
                                 expected_product.identity())

    def test_plain_defconfig_delegates_to_the_default_kernel_config(self):
        result = subprocess.run(
            ['make', '--no-print-directory', '-n', 'defconfig', 'ARCH=aarch64',
             f'SYSTEM_CONFIG={self.root / "product.config"}'],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'--kernel-input "{ROOT}/kernel/configs/qemu_aarch64_defconfig"', result.stdout)
        self.assertIn(f'--kernel-output "{ROOT}/kernel/.config"', result.stdout)

    def test_kernel_generation_and_output_failures_preserve_both_configs(self):
        product = self.root / 'product.config'
        kernel = self.root / 'kernel.config'
        for path in (product, kernel):
            path.write_text('original configuration\n')
        invalid = self.root / 'invalid-kernel-defconfig'
        invalid.write_text('CONFIG_KERNEL_STACK_SIZE_KB=24\n')
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/product-config.py'), 'defconfig',
             '--input', str(ROOT / 'configs/default_defconfig'), '--output', str(product),
             '--kernel-input', str(invalid), '--kernel-output', str(kernel)],
            cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('power of two', result.stderr)
        for path in (product, kernel):
            self.assertEqual(path.read_text(), 'original configuration\n')
        # Both inputs are valid, but the second output cannot be prepared.
        for output in (self.root / 'missing-directory/kernel.config', self.root):
            result = subprocess.run(
                ['make', '--no-print-directory', 'defconfig', f'SYSTEM_CONFIG={product}',
                 f'CONFIG_FILE={output}'],
                cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(product.read_text(), 'original configuration\n')
            self.assertFalse(list(self.root.glob('.hyper-config-*')))

    def test_defconfig_rejects_overlapping_outputs_before_writing(self):
        kernel = self.root / 'shared.config'
        kernel.write_text('keep this configuration\n')
        alias = self.root / 'product.config'
        alias.symlink_to(kernel)
        result = subprocess.run(
            ['make', '--no-print-directory', 'defconfig',
             f'SYSTEM_CONFIG={alias}', f'CONFIG_FILE={kernel}'],
            cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must select different files', result.stderr)
        self.assertEqual(kernel.read_text(), 'keep this configuration\n')
        self.assertTrue(alias.is_symlink())

    def test_product_kernel_section_cannot_enable_test_hooks(self):
        for name in ('kernel-test', 'io-dma-revoke-test', 'kernel-self-test', 'missing'):
            with self.subTest(name=name):
                path = self.root / 'product.config'
                path.write_text('CONFIG_KERNEL_FEATURES=' + system_config.quoted(name) + '\n')
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
            if not path.resolve().is_relative_to(self.root.resolve()):
                # Literal component sources are repository payloads, not build
                # outputs. Copy them into this fixture; never replace source
                # notices or configuration with the synthetic executable.
                payload = path.read_bytes()
                path = self.root / 'payloads' / entry['destination']
                entry['source'] = str(path)
            else:
                payload = elf
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        manifest = self.root / 'registry.json'
        manifest.write_text(json.dumps({'version': 1, 'entries': self.entries}))
        output = self.root / 'output'
        output.mkdir()
        io_image = output / 'io.itb'
        io_image.write_bytes(b'fixture I/O VM FIT')
        (output / 'io.entries.json').write_text(json.dumps([
            ['0644', 'vm/io.itb', str(io_image)],
            ['0644', 'etc/hyper/board.json', str(ROOT / 'boards/qemu-minimal.json')],
            *[['0644', 'etc/hyper/' + name, str(board_output / name)]
              for name in ('storage.json', 'filesystem.json')],
        ]))
        arguments = ['pack-native-system', '--board', str(ROOT / 'boards/qemu-minimal.json'),
                     '--board-output', str(board_output), '--deployment', str(manifest),
                     '--system-config', str(self.root / 'product.config'), '--apps', str(apps),
                     '--boot-apps', str(boot), '--sdk', str(sdk), '--std', str(self.root / 'std'),
                     '--output', str(output), '--arch', 'aarch64', '--packer', 'unused-packer',
                     '--strip', 'unused-strip', '--boot-components', *system_config.ProductConfig({'version': 1}).boot_components(self.entries)]
        # Run real board composition and staging; only external ELF stripping,
        # manifest executables and cpio creation are outside this Python test.
        policies = []
        for explicit in (True, False):
            selected_arguments = list(arguments)
            if not explicit:
                index = selected_arguments.index('--system-config')
                del selected_arguments[index:index + 2]
            real_run = subprocess.run
            def run(command, *args, **kwargs):
                if Path(command[0]).name in ('make', 'hyper-kconfig'):
                    return real_run(command, *args, **kwargs)
                return subprocess.CompletedProcess(command, 0)
            with patch.object(sys, 'argv', selected_arguments), patch.object(split.subprocess, 'run', run):
                split.main()
            policies.append(json.loads((output / 'system-root/etc/hyper/startup.json').read_text()))
        self.assertEqual(policies[0], policies[1])
        startup = policies[0]
        self.assertNotIn('mounts', startup)
        manager = next(app for app in startup['applications'] if app['component'] == '/vm-manager')
        self.assertEqual(manager['fleet'], '/etc/hyper/vms.json')
        fleet = output / 'system-root' / manager['fleet'].lstrip('/')
        self.assertEqual(json.loads(fleet.read_text()),
                         {'format': 'hyper.vm-config', 'virtual-machines': []})
        boot_entries = json.loads((output / 'native-system/boot.entries.json').read_text())
        self.assertNotIn('etc/hyper/vms.json', {name for _, name, _ in boot_entries})


if __name__ == '__main__':
    unittest.main()
