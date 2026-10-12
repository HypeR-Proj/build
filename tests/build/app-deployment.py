#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check deployment profiles, overrides and timestamp-preserving installation."""

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/app-deployment.py'
MANIFEST = ROOT / 'mk/components.mk'
spec = importlib.util.spec_from_file_location('deployment', SCRIPT)
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
split_spec = importlib.util.spec_from_file_location('split_system', ROOT / 'scripts/pack-native-system.py')
split = importlib.util.module_from_spec(split_spec)
split_spec.loader.exec_module(split)

elf_spec = importlib.util.spec_from_file_location('elf_fixtures', ROOT / 'tests/build/native-libraries.py')
elf_fixtures = importlib.util.module_from_spec(elf_spec)
elf_spec.loader.exec_module(elf_fixtures)


class DeploymentTests(unittest.TestCase):
    def test_runtime_graphs_keep_console_workers_out_of_std_builds(self):
        outputs = {}
        for runtime in ('core', 'std'):
            outputs[runtime] = subprocess.check_output([
                sys.executable, str(SCRIPT), 'cargo-targets', '--manifest', str(MANIFEST),
                '--link-mode', 'static', '--rust-runtime', runtime,
                '--extra-bin', 'hyper-storage-probe',
                '--features', 'hyper-io-runtime/storage-probe'], text=True).split()
        for name in ('hyper-console-input', 'hyper-console-output'):
            self.assertIn(name, outputs['core'])
            self.assertNotIn(name, outputs['std'])
        self.assertNotIn('hyper-storage-probe', outputs['core'])
        self.assertIn('hyper-storage-probe', outputs['std'])
        self.assertNotIn('--features', outputs['core'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standard, core, output = root / 'standard', root / 'core', root / 'output'
            standard.mkdir()
            core.mkdir()
            (standard / 'hyper-init').write_bytes(elf_fixtures.elf())
            (standard / 'hyper-console-input').write_bytes(b'stale std image')
            (core / 'hyper-console-input').write_bytes(elf_fixtures.elf())
            subprocess.run([
                sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                '--build', str(standard), '--static-core-build', str(core),
                '--output', str(output), '--component', 'init',
                '--component', 'console-input'], check=True)
            self.assertEqual((output / 'console-input').read_bytes(), elf_fixtures.elf())

    def test_component_selection_builds_and_installs_only_requested_executables(self):
        selection = ['--component', 'init', '--component', 'fallback-sh']
        result = subprocess.check_output([
            sys.executable, str(SCRIPT), 'cargo-targets', '--manifest', str(MANIFEST),
            *selection], text=True)
        self.assertEqual(result.split(), [
            '--package', 'hyper-fallback-sh', '--package', 'hyper-init',
            '--bin', 'hyper-fallback-sh', '--bin', 'hyper-init'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, output = root / 'build', root / 'output'
            build.mkdir()
            for binary in ('hyper-init', 'hyper-fallback-sh'):
                (build / binary).write_bytes(elf_fixtures.elf())
            # No libraries or unrelated applications exist in this build tree.
            subprocess.run([
                sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                '--build', str(build), '--output', str(output), *selection], check=True)
            self.assertEqual({path.name for path in output.iterdir()}, {'init', 'fallback-sh'})
        entries = deployment.load(MANIFEST)
        for components in (['missing'], ['lib/libhyper_rust_std.so']):
            with self.subTest(components=components), self.assertRaises(ValueError):
                deployment.select_components(entries, components)

    def test_link_graphs_route_probes_and_features_by_source_owner(self):
        entries = deployment.load(MANIFEST)
        arguments = ['--features', 'hyper-io-runtime/storage-probe,hyper-vm-runtime/broker-test',
                     '--extra-bin', 'hyper-storage-probe']
        outputs = {}
        for mode in ('static', 'dynamic'):
            outputs[mode] = subprocess.check_output([
                sys.executable, str(SCRIPT), 'cargo-targets', '--manifest', str(MANIFEST),
                '--link-mode', mode, *arguments], text=True).split()
        self.assertIn('hyper-storage-probe', outputs['static'])
        self.assertIn('hyper-io-runtime/storage-probe', outputs['static'])
        self.assertNotIn('hyper-vm-runtime/broker-test', outputs['static'])
        self.assertIn('hyper-vm-runtime/broker-test', outputs['dynamic'])
        self.assertNotIn('hyper-storage-probe', outputs['dynamic'])
        for entry in entries:
            if 'package' in entry:
                other = 'static' if entry['link_mode'] == 'dynamic' else 'dynamic'
                self.assertIn(entry['package'], outputs[entry['link_mode']])
                self.assertNotIn(entry['package'], outputs[other])
        result = subprocess.run([
            sys.executable, str(SCRIPT), 'cargo-targets', '--manifest', str(MANIFEST),
            '--link-mode', 'dynamic', '--component', 'init'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('required link mode', result.stderr)

    def test_bootstrap_install_rejects_dynamic_artifacts_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, output = root / 'build', root / 'output'
            build.mkdir()
            output.mkdir()
            (output / 'init').write_bytes(b'previous image')
            for options in ({'interpreter': '/lib64/ld-hyper-aarch64.so'},
                            {'needed': ['libhyper.so']}, {'soname': 'libbad.so'}):
                (build / 'hyper-init').write_bytes(elf_fixtures.elf())
                (build / 'hyper-fallback-sh').write_bytes(elf_fixtures.elf(**options))
                result = subprocess.run([
                    sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                    '--build', str(build), '--output', str(output),
                    '--component', 'init', '--component', 'fallback-sh'], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('requires a static ELF', result.stderr)
                self.assertEqual((output / 'init').read_bytes(), b'previous image')
                self.assertFalse((output / 'fallback-sh').exists())

    def test_mixed_install_uses_static_tree_for_bootstrap_and_its_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, static, output = root / 'dynamic', root / 'static', root / 'output'
            build.mkdir()
            static.mkdir()
            for name in ('hyper-io-runtime', 'hyper-storage-probe'):
                (static / name).write_bytes(elf_fixtures.elf())
                (build / name).write_bytes(b'stale dynamic artifact')
            (build / 'hyper-ls').write_bytes(b'ordinary application')
            subprocess.run([
                sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                '--build', str(build), '--static-build', str(static), '--output', str(output),
                '--component', 'io-runtime', '--component', 'ls',
                '--extra-bin', 'hyper-storage-probe'], check=True)
            self.assertEqual((output / 'io-runtime').read_bytes(), elf_fixtures.elf())
            self.assertEqual((output / 'storage-probe').read_bytes(), elf_fixtures.elf())
            self.assertEqual((output / 'ls').read_bytes(), b'ordinary application')

    def test_registry_checks_cargo_targets_and_derives_host_exclusions(self):
        entries = deployment.load(MANIFEST)
        result = subprocess.check_output([sys.executable, str(SCRIPT), 'host-excludes',
                                          '--manifest', str(MANIFEST)], text=True)
        self.assertEqual(result.split(), [value for entry in entries if 'library' in entry
                                         for value in ('--exclude', entry['package'])])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.mk'
            for record in ('binary|app/echo|missing-binary|echo|bin/echo|system',
                           'library|app/echo|libhyper_echo.so|lib/libhyper_echo.so|lib/libhyper_echo.so|system',
                           'binary|../outside|escape|escape|bin/escape|system'):
                path.write_text("component-records:\n\t@printf '%s\\n' '" + record + "'\n")
                with self.subTest(record=record), self.assertRaises(ValueError):
                    deployment.load(path)

    def test_system_retains_apps_without_acceptance_programs(self):
        roots = dict(apps='/apps', sdk='/sdk', std='/std', arch='riscv64')
        system = deployment.compose(MANIFEST, 'system', roots)
        development = deployment.compose(MANIFEST, 'development', roots)
        system_names = set(system[1::3])
        link = system.index('lib')
        self.assertEqual(system[link - 1:link + 2], ['symlink', 'lib', 'lib64'])
        self.assertTrue({'init', 'bin/sh', 'bin/cp', 'bin/vmm', 'bin/ldd', 'svc/vm-manager',
                         'lib', 'lib64/ld-hyper-riscv64.so',
                         'lib64/riscv64-hyper-hyper/libhyper.so'} <= system_names)
        # Rust libraries are selected from DT_NEEDED after executable overrides.
        self.assertNotIn('lib64/riscv64-hyper-hyper/libhyper_clap_shared.so', system_names)
        libraries = [entry for entry in deployment.load(MANIFEST) if 'library' in entry]
        self.assertIn('libhyper_clap_shared.so', [entry['library'] for entry in libraries])
        self.assertTrue(system_names < set(development[1::3]))
        for fixture in ('bin/echo-static', 'bin/dynamic-test', 'bin/std-test', 'bin/std-test-static'):
            self.assertNotIn(fixture, system_names)
            self.assertIn(fixture, development[1::3])
        replaced = deployment.compose(MANIFEST, 'system', roots, ['bin/ps=/probe'])
        self.assertEqual(replaced[replaced.index('bin/ps') + 1], '/probe')
        with self.assertRaises(ValueError):
            deployment.compose(MANIFEST, 'system', roots, ['bin/typo=/probe'])

    def test_invalid_manifest_fails_before_installation(self):
        original = {'version': 1, 'entries': deployment.load(MANIFEST)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for field, value in [('destination', '../escape'), ('mode', '0999'),
                                 ('binary', 'bad;command'), ('profiles', ['typo'])]:
                with self.subTest(field=field):
                    data = json.loads(json.dumps(original))
                    data['entries'][0][field] = value
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        deployment.load(path)
            original['entries'].append(original['entries'][0])
            path.write_text(json.dumps(original))
            with self.assertRaises(ValueError):
                deployment.load(path)

    def test_install_reuses_outputs_and_includes_io_service(self):
        with tempfile.TemporaryDirectory(prefix='hyper deployment ') as directory:
            root = Path(directory)
            build, output = root / 'build', root / 'output'
            build.mkdir()
            programs = [entry for entry in deployment.load(MANIFEST) if 'binary' in entry]
            self.assertIn('hyper-io-runtime', [entry['binary'] for entry in programs])
            self.assertNotIn('hyper-io-smoke', [entry['binary'] for entry in programs])
            for entry in programs:
                (build / entry['binary']).write_bytes(elf_fixtures.elf())
            for entry in deployment.load(MANIFEST):
                if 'library' in entry:
                    (build / entry['library']).write_text(entry['library'])
            command = [sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                       '--build', str(build), '--output', str(output)]
            subprocess.run(command, check=True)
            before = {path.name: path.stat().st_mtime_ns for path in output.iterdir()}
            subprocess.run(command, check=True)
            self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in output.iterdir()})
            self.assertEqual((output / 'sh').read_bytes(), elf_fixtures.elf())
            self.assertEqual((output / 'sh').stat().st_mode & 0o777, 0o755)
            self.assertEqual((output / 'lib/libhyper_tool_args_shared.so').read_text(),
                             'libhyper_tool_args_shared.so')

    def test_library_artifacts_require_exact_safe_names_and_unique_sources(self):
        original = {'version': 1, 'entries': deployment.load(MANIFEST)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for name in ('../libbad.so', 'libbad.so;command', 'bad.so', 'libbad.a'):
                data = json.loads(json.dumps(original))
                library = next(entry for entry in data['entries'] if 'library' in entry)
                library['library'] = name
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    deployment.load(path)
            library = next(entry for entry in original['entries'] if 'library' in entry)
            library['source'] = '/ambiguous'
            path.write_text(json.dumps(original))
            with self.assertRaises(ValueError):
                deployment.load(path)

    def test_symlink_manifest_requires_a_relative_target_and_cannot_be_replaced_as_a_file(self):
        roots = dict(apps='/apps', sdk='/sdk', std='/std', arch='aarch64')
        with self.assertRaises(ValueError):
            deployment.compose(MANIFEST, 'system', roots, ['lib=/host/regular-file'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for target in ('.', '../lib64', '/lib64', 'lib64//other', 'bad\0path'):
                data = {'version': 1, 'entries': deployment.load(MANIFEST)}
                next(entry for entry in data['entries'] if 'symlink' in entry)['symlink'] = target
                path.write_text(json.dumps(data))
                with self.subTest(target=target), self.assertRaises(ValueError):
                    deployment.load(path)


class NativePayloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.args = SimpleNamespace(
            apps=self.root / 'apps', boot_apps=self.root / 'boot-apps',
            sdk=self.root / 'sdk', std=self.root / 'std', arch='aarch64',
            deployment=self.root / 'components.json', board=self.root / 'board.json',
            extra_entries=[], profile='system', replace=[], boot_components=['init', 'fallback-sh', 'fs-fat'])
        self.entries = []
        # ELF-shaped data exercises real dependency inspection without a host
        # cross compiler. Program headers are added in the dynamic-core case.
        self.elf = bytearray(64)
        self.elf[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', self.elf, 18, 183)
        struct.pack_into('<H', self.elf, 54, 56)
        for name, destination in [('init', 'init'), ('fallback-sh', 'svc/fallback-sh'), ('sh', 'bin/sh'),
                                  ('fs-fat', 'svc/fs-fat'), ('ls', 'bin/ls'),
                                  ('vm-manager', 'svc/vm-manager'),
                                  ('vm-runtime', 'svc/vm-runtime'),
                                  ('init-system', 'svc/init-system')]:
            self.entries.append({'binary': 'hyper-' + name, 'staged': name,
                                 'destination': destination, 'mode': '0755',
                                 'profiles': ['system']})
            for directory in (self.args.apps, self.args.boot_apps):
                directory.mkdir(exist_ok=True)
                (directory / name).write_bytes(self.elf)
        for name in ('ld-hyper-aarch64.so', 'userspace-loader-hyper-aarch64'):
            source = self.args.sdk / 'lib64' / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(self.elf)
            self.entries.append({'source': str(source), 'destination': 'lib64/' + name,
                                 'mode': '0755', 'profiles': ['system']})
        self.entries.append({'symlink': 'lib64', 'destination': 'lib',
                             'mode': '0777', 'profiles': ['system']})
        from native_deployment import prepare
        self.board = prepare(json.loads((ROOT / 'boards/qemu.json').read_text()), 'split')

    def compose(self):
        self.args.deployment.write_text(json.dumps({'version': 1, 'entries': self.entries}))
        self.args.board.write_text(json.dumps(self.board))
        return split.compose_entries(self.args, self.board['native'])

    def test_init_policies_are_packaged_in_their_own_environments(self):
        self.args.output = self.root / 'output'
        self.args.initramfs = self.root / 'bootstrap.cpio'
        self.args.packer = 'unused-packer'
        self.args.strip = 'unused-strip'
        graph = self.root / 'services.json'
        graph.write_text(json.dumps({'format': 'hyper.service-manifest', 'services': []}))
        # Exercise real metadata generation and payload staging; this fixture
        # contains no ELF programs and does not invoke external build tools.
        with patch.object(split, 'compose_entries', return_value=([], [], {})), \
                patch.object(split.subprocess, 'run'):
            split.compose(self.args, self.board['native'], graph)
        entries = json.loads((self.args.output / 'native-system/boot.entries.json').read_text())
        boot_config = next(Path(source) for _, name, source in entries
                           if name == 'etc/hyper/init.json')
        self.assertEqual(json.loads(boot_config.read_text()),
                         {'system-init': '/system/svc/init-system'})
        system = self.args.output / 'system-root'
        self.assertEqual(json.loads((system / 'etc/hyper/init.json').read_text()), {
            'services': '/system/etc/hyper/services.json',
            'root-switch': split.root_switch_policy(self.board['native']),
        })
        self.assertEqual((system / 'etc/fstab').read_text(), self.board['native']['fstab'])

    def test_boot_is_static_and_keeps_system_services_separate(self):
        boot, disk, _ = self.compose()
        links = {name: target for mode, name, target in boot if mode == 'symlink'}
        from native_libraries import resolve
        self.assertEqual(resolve('init', links), 'init')
        self.assertEqual(resolve('svc/fallback-sh', links), 'svc/fallback-sh')
        self.assertEqual(resolve('svc/fs-fat', links), 'svc/fs-fat')
        self.assertNotIn('svc/vm-runtime', {name for _, name, _ in boot})
        self.assertNotIn('svc/vm-manager', {name for _, name, _ in boot})
        self.assertEqual(resolve('lib/userspace-loader-hyper-aarch64', links),
                         'lib64/userspace-loader-hyper-aarch64')
        self.assertNotIn('lib64/ld-hyper-aarch64.so', {name for _, name, _ in boot})
        disk_names = {name for _, name, _ in disk}
        self.assertTrue({'bin/sh', 'bin/ls', 'svc/init-system', 'svc/vm-manager',
                         'lib64/ld-hyper-aarch64.so'} <= disk_names)
        self.assertTrue({'init', 'svc/fallback-sh', 'svc/fs-fat', 'lib64/userspace-loader-hyper-aarch64'}
                        .isdisjoint(disk_names))

    def test_missing_erofs_worker_does_not_gate_an_erofs_system_build(self):
        # The deployment intentionally lacks fs-erofs in both RAM and on disk.
        # Only runtime magic detection/worker lookup may reject this mount.
        boot, disk, _ = self.compose()
        system = next(partition for partition in self.board['disk']['partitions']
                      if partition.get('content') == 'system')
        self.assertEqual(system['filesystem'], 'erofs')
        self.assertFalse(any(name.endswith('/fs-erofs') for _, name, _ in boot + disk))

    def test_data_worker_can_live_in_system_without_entering_boot(self):
        self.args.boot_components.remove('fs-fat')
        boot, system, _ = self.compose()
        self.assertNotIn('svc/fs-fat', {name for _, name, _ in boot})
        self.assertIn('svc/fs-fat', {name for _, name, _ in system})

    def test_ramfs_uses_the_same_core_and_formal_payload_as_disk(self):
        boot, disk, _ = self.compose()
        ramfs = {'layout': 'ramfs', 'volumes': []}
        ram_boot, ram_system, _ = split.compose_entries(self.args, ramfs)
        self.assertEqual(ram_boot, boot)
        self.assertEqual(ram_system, disk)
        self.assertIn('svc/init-system', {name for _, name, _ in ram_system})

    def test_root_switch_keeps_disk_mounts_and_binds_ramfs_without_copying(self):
        policy = split.root_switch_policy(self.board['native'])
        self.assertEqual(policy['put-old'], '/boot')
        self.assertEqual(policy.get('links', []), [])
        self.assertEqual(policy['mounts'], [
            {'source': '/system', 'target': '/system', 'ramfs': False},
        ])
        self.assertIn('/data', policy['directories'])
        ramfs = {'layout': 'ramfs', 'boot-fstab': 'media /media/my\\040disk auto noauto 0 0\n'}
        policy = split.root_switch_policy(ramfs)
        self.assertTrue(policy['mounts'][0]['ramfs'])
        self.assertIn({'source': '/media/my disk', 'target': '/media/my disk',
                       'optional': True}, policy['mounts'])

    def test_root_switch_preserves_literal_hashes_in_fstab_fields(self):
        policy = split.root_switch_policy({
            'layout': 'split',
            'boot-fstab': ('# ignored comment\n'
                      'PARTLABEL=archive#1 /archive auto rw 0 0 # comment\n'
                      'PARTLABEL=archive2 /archive#2 auto rw 0 0\n'
                      'PARTLABEL=archive3 /archive\\040#3 auto rw 0 0\n'),
        })
        for target in ('/archive', '/archive#2', '/archive #3'):
            self.assertIn({'source': target, 'target': target, 'optional': True},
                          policy['mounts'])
        self.assertEqual(len(policy['mounts']), 4)

    def test_formal_mount_policy_cannot_change_root_switch(self):
        native = {'layout': 'split', 'boot-fstab': ''}
        expected = split.root_switch_policy(native)
        native['fstab'] = 'PARTLABEL=archive /boot/archive auto rw 0 0\n'
        self.assertEqual(split.root_switch_policy(native), expected)

    def test_extra_data_stays_in_boot_and_extra_elf_uses_system(self):
        guest = self.root / 'guest.itb'
        guest.write_bytes(b'guest FIT payload')
        config = self.root / 'vms.json'
        config.write_text('{}')
        probe = self.root / 'probe'
        probe.write_bytes(self.elf)
        self.args.extra_entries = ['0644', 'vm/guest.itb', str(guest),
                                   '0644', 'etc/hyper/vms.json', str(config),
                                   '0755', 'bin/probe', str(probe)]
        boot, system, _ = self.compose()
        self.assertIn(['0644', 'vm/guest.itb', str(guest)], boot)
        self.assertIn(['0644', 'etc/hyper/vms.json', str(config)], boot)
        self.assertIn(['0755', 'bin/probe', str(probe)], system)
        self.assertNotIn('bin/probe', {name for _, name, _ in boot})

    def test_extra_core_override_is_packaged_once_and_validated(self):
        probe = self.root / 'replacement'
        probe.write_bytes(self.elf)
        self.args.extra_entries = ['0755', 'svc/fs-fat', str(probe)]
        boot, system, _ = self.compose()
        self.assertEqual([entry for entry in boot if entry[1] == 'svc/fs-fat'],
                         [['0755', 'svc/fs-fat', str(probe)]])
        self.assertNotIn('svc/fs-fat', {name for _, name, _ in system})
        probe.write_bytes(elf_fixtures.elf(interpreter='/lib64/ld-hyper-aarch64.so'))
        with self.assertRaisesRegex(ValueError, 'must be a static ELF'):
            self.compose()

    def test_replacements_and_development_profile_keep_their_roles(self):
        boot_replacement = self.root / 'new-sh'
        system_replacement = self.root / 'new-ls'
        for path in (boot_replacement, system_replacement):
            path.write_bytes(self.elf)
        self.args.replace = [f'svc/fallback-sh={boot_replacement}', f'bin/ls={system_replacement}']
        boot, system, _ = self.compose()
        self.assertIn(['0755', 'svc/fallback-sh', str(boot_replacement)], boot)
        self.assertIn(['0755', 'bin/ls', str(system_replacement)], system)
        probe = self.root / 'development-probe'
        probe.write_bytes(self.elf)
        for entry in self.entries:
            entry['profiles'].append('development')
        self.entries.append({'source': str(probe), 'destination': 'bin/probe',
                             'mode': '0755', 'profiles': ['development']})
        self.assertNotIn('bin/probe', {name for _, name, _ in self.compose()[1]})
        self.args.profile = 'development'
        self.assertIn(['0755', 'bin/probe', str(probe)], self.compose()[1])

    def test_ambiguous_additional_sources_are_rejected(self):
        self.args.extra_entries = ['0644', 'etc/data', 'relative-path']
        with self.assertRaisesRegex(ValueError, 'absolute paths'):
            self.compose()
        self.args.extra_entries = ['0644', 'etc/data', '/one', '0644', 'etc/data', '/two']
        with self.assertRaisesRegex(ValueError, 'duplicate archive path'):
            self.compose()

    def test_fat_system_omits_optional_alias_but_rejects_other_links(self):
        system = next(partition for partition in self.board['disk']['partitions']
                      if partition.get('content') == 'system')
        system['filesystem'] = 'fat'
        _, disk, _ = self.compose()
        self.assertFalse(any(mode == 'symlink' for mode, _, _ in disk))
        self.entries.append({'symlink': 'ls', 'destination': 'bin/alias',
                             'mode': '0777', 'profiles': ['system']})
        with self.assertRaisesRegex(ValueError, 'cannot represent symbolic links'):
            self.compose()

    def test_dynamic_core_is_rejected_before_payload_publication(self):
        interpreter = b'/lib64/ld-hyper-aarch64.so\0'
        image = self.elf + bytes(56) + interpreter
        struct.pack_into('<Q', image, 32, 64)
        struct.pack_into('<H', image, 56, 1)
        struct.pack_into('<IIQQQQQQ', image, 64, 3, 4, 120, 0, 0,
                         len(interpreter), len(interpreter), 1)
        (self.args.boot_apps / 'fallback-sh').write_bytes(image)
        with self.assertRaisesRegex(ValueError, 'must be a static ELF'):
            self.compose()

    def test_changed_inputs_preserve_previous_system_staging(self):
        source = self.root / 'input'
        source.write_bytes(b'first build')
        destination = self.root / 'system'
        destination.mkdir()
        (destination / 'old').write_bytes(b'previous checked build')
        checksums = {str(source): split.archive.digest(source)}
        copyfile = shutil.copyfile

        def changed_during_copy(original, target):
            copyfile(original, target)
            source.write_bytes(b'second build')

        with patch.object(split.shutil, 'copyfile', side_effect=changed_during_copy):
            with self.assertRaisesRegex(ValueError, 'changed during composition'):
                split.stage_system([['0644', 'input', str(source)]], destination,
                                   '/unused/strip', checksums)
        self.assertEqual(list(destination.iterdir()), [destination / 'old'])
        self.assertEqual((destination / 'old').read_bytes(), b'previous checked build')


if __name__ == '__main__':
    unittest.main()
