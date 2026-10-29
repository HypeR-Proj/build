#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check product menus against real Cargo selection and atomic save semantics."""

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'kernel/tools/kconfig/tests'))
sys.path.insert(0, str(ROOT / 'scripts'))
import menuconfig
import system_config
from product_fixture import write_product
from native_components import load_registry
from terminal import Terminal


class ProductMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = load_registry(ROOT / 'mk/components.mk')['entries']

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hyper product menu ')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'product.config'

    def model(self, value=None):
        if value is not None:
            write_product(self.path, value)
        return menuconfig.Configuration(self.path, self.entries)

    def deployment(self, action, *extra):
        return subprocess.check_output(
            [sys.executable, '-B', str(ROOT / 'scripts/app-deployment.py'), action,
             '--manifest', str(ROOT / 'mk/components.mk'), '--system-config', str(self.path),
             '--profile', 'system', *extra], cwd=ROOT, text=True)

    def test_selections_drive_build_targets_and_deployment_independently(self):
        config = self.model()
        config.update('CONFIG_BOOT_APP_FS_FAT', 'n')
        config.update('CONFIG_SYSTEM_APP_EDITOR', 'n')
        config.save()
        selected = json.loads(self.deployment('manifest'))['entries']
        names = {entry['staged'] for entry in selected if 'binary' in entry}
        self.assertNotIn('fs-fat', names)
        self.assertNotIn('editor', names)
        self.assertIn('fs-fat-system', names)
        self.assertNotIn('fs-fat', self.deployment('boot-components').split())
        targets = self.deployment('cargo-targets', '--link-mode', 'dynamic', '--executable-roots').split()
        self.assertNotIn('hyper-editor', targets)
        self.assertIn('hyper-fs-fat-system', targets)
        config.update('CONFIG_SYSTEM_APP_FS_FAT_SYSTEM', 'n')
        config.save()
        self.assertNotIn('hyper-fs-fat-system', self.deployment('cargo-targets').split())

    def test_prerequisite_changes_cascade_and_rejected_edits_preserve_state(self):
        config = self.model({'version': 1})
        config.update('CONFIG_SYSTEM_APP_TERMINAL_SERVICE', 'n')
        self.assertFalse({'session-manager', 'terminal-service', 'editor'} & config.selected['system'])
        before = dict(config.selected)
        with self.assertRaisesRegex(ValueError, 'CONFIG_SYSTEM_APP_SESSION_MANAGER.*requires'):
            config.update('CONFIG_SYSTEM_APP_SESSION_MANAGER', 'y')
        self.assertEqual(config.selected, before)
        config.save()
        config.update('CONFIG_SYSTEM_APP_TERMINAL_SERVICE', 'y')
        config.update('CONFIG_SYSTEM_APP_SESSION_MANAGER', 'y')
        config.save()
        self.assertIn('hyper-session-manager', self.deployment('binaries').split())

    def test_core_programs_remain_independent(self):
        config = self.model()
        config.update('CONFIG_SYSTEM_APP_SVC_JOURNAL', 'n')
        self.assertNotIn('svc/journal', config.selected['system'])
        self.assertNotIn('system-startup', config.selected['system'])
        self.assertTrue({'component-manager', 'console-raw-system', 'svc/bootstrap-retire'} <= config.selected['system'])
        config.save()
        config.update('CONFIG_SYSTEM_APP_SVC_JOURNAL', 'y')
        config.update('CONFIG_SYSTEM_APP_SYSTEM_STARTUP', 'y')
        config.save()

    def test_existing_profiles_and_feature_policy_survive_menu_edits(self):
        value = {'version': 1, 'SPDX-License-Identifier': 'Apache-2.0',
                 'kernel': {'features': ['runtime-symbols']},
                 'boot-profiles': {'default': {
                     'version': 1, 'services': '/etc/hyper/services.json',
                     'io': {'kind': 'none'}, 'requires': []}},
                 'system': {'base': 'none', 'include': ['echo'], 'storage-providers': []}}
        config = self.model(value)
        config.update('CONFIG_SYSTEM_APP_LS', 'y')
        config.save()
        saved = system_config.load(self.path).value
        for field in ('kernel', 'boot-profiles'):
            self.assertEqual(saved[field], value[field])
        self.assertEqual(saved['system']['storage-providers'], [])
        self.assertEqual(config.selected['system'], {'ls', 'echo'})
        self.assertEqual(saved['system']['base'], 'none')

    def test_registrations_appear_without_a_parallel_menu_catalog(self):
        config = self.model()
        self.assertEqual({entry['name'] for entry in config.state['entries'] if entry['kind'] == 'bool'},
                         {name for name in system_config.component_symbols(self.entries)
                          if menuconfig.kconfig.schema()[name]['prompt']})

    def terminal(self, command=None):
        terminal = Terminal(command or [sys.executable, '-B', str(ROOT / 'scripts/menuconfig.py'),
                                        '--output', str(self.path), '--kernel-config',
                                        str(self.directory / 'kernel.config')], cwd=ROOT)
        self.addCleanup(terminal.close)
        terminal.expect(b'HypeR System Configuration')
        return terminal

    def test_real_navigation_into_stage1_filesystems(self):
        terminal = self.terminal()
        # Bootstrap stage1 -> fs-fat.
        terminal.send(b'j\njjjjn')
        terminal.expect(b'not yet saved')
        terminal.send(b'sqq')
        terminal.finish()
        self.assertNotIn('fs-fat', system_config.load(self.path).boot_components(self.entries))

    def test_search_dependency_error_then_restore_prerequisite(self):
        self.model({'version': 1})
        terminal = self.terminal()
        terminal.send(b'/CONFIG_SYSTEM_APP_TERMINAL_SERVICE\nnq/CONFIG_SYSTEM_APP_SESSION_MANAGER\ny')
        terminal.expect(b'Invalid value; setting unchanged')
        terminal.send(b'\nq/CONFIG_SYSTEM_APP_TERMINAL_SERVICE\nyq/CONFIG_SYSTEM_APP_SESSION_MANAGER\nysqq')
        terminal.finish()
        self.assertTrue({'session-manager', 'terminal-service'} <=
                        system_config.load(self.path).selected_names(self.entries, 'system'))

    def test_cancellation_does_not_create_a_product(self):
        terminal = self.terminal()
        terminal.send(b'qd')
        terminal.finish()
        self.assertFalse(self.path.exists())

    def test_nested_kernel_entry_preserves_product_edits_and_terminal(self):
        # Kernel's real curses UI has its own Rust/PTY acceptance tests. Stub only
        # the child invocation here; product parsing still uses the real backend.
        child = self.directory / 'make'
        child.write_text(f'#!{sys.executable}\nimport json, os, sys\n'
                         f'if \"menuconfig\" not in sys.argv:\n    os.execv({shutil.which("make")!r}, sys.argv)\n'
                         + '''from pathlib import Path
Path(__file__).with_suffix('.args').write_text(json.dumps(sys.argv[1:]))
print('Kernel submenu probe', flush=True)
input()
''')
        child.chmod(0o755)
        with patch.dict(os.environ, PATH=str(self.directory) + os.pathsep + os.environ['PATH']):
            terminal = self.terminal()
        terminal.send(b'/CONFIG_SYSTEM_APP_EDITOR\nnq\n\n')
        terminal.expect(b'Kernel submenu probe')
        terminal.send(b'q\n')
        terminal.expect(b'Returned from kernel menu')
        terminal.send(b'sqq')
        terminal.finish()
        self.assertIn('CONFIG_FILE=' + str((self.directory / 'kernel.config').resolve()),
                      json.loads(child.with_suffix('.args').read_text()))
        self.assertNotIn('editor', system_config.load(self.path).selected_names(self.entries, 'system'))

    def test_noninteractive_menu_fails_without_creating_output(self):
        result = subprocess.run(['make', '--no-print-directory', 'menuconfig',
                                 'SYSTEM_CONFIG=' + str(self.path)], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('interactive terminal', result.stderr)
        self.assertNotIn('invalid SYSTEM_CONFIG', result.stderr)
        self.assertFalse(self.path.exists())

    def test_automatic_local_product_and_explicit_override_reach_make(self):
        workspace = self.directory / 'workspace'
        workspace.mkdir()
        for source in ROOT.iterdir():
            if source.name not in ('.git', '.agent', '.config', 'target'):
                (workspace / source.name).symlink_to(source)
        config_path = workspace / '.config'
        config_path.write_text('# CONFIG_BOOT_APP_FS_FAT is not set\n')
        probe = self.directory / 'probe.mk'
        probe.write_text('__product:\n\t@printf "%s\\n" "$(SYSTEM_CONFIG)" "$(APP_BOOT_COMPONENTS)"\n')
        def make(*arguments):
            return subprocess.check_output(
                ['make', '--no-print-directory', '-s', '-f', 'Makefile', '-f', str(probe),
                 '__product', *arguments], cwd=workspace, text=True).splitlines()
        selected = make()
        self.assertEqual(selected[0], '.config')
        self.assertNotIn('fs-fat', selected[1].split())
        default = make('SYSTEM_CONFIG=')
        self.assertEqual(default[0], '')
        self.assertIn('fs-fat', default[1].split())
        self.path.write_text('')
        explicit = make('SYSTEM_CONFIG=' + str(self.path))
        self.assertEqual(explicit[0], str(self.path))
        self.assertIn('fs-fat', explicit[1].split())


if __name__ == '__main__':
    unittest.main()
