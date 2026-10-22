#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check the packed dependency closure of every selectable bootstrap profile."""

import copy
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
import boot_profiles
import system_config
from board_config import Board
from board_bootstrap import services


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hyper profile ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.records = []
        self.profile = {'version': 1, 'services': '/etc/hyper/services.json',
                        'io': {'kind': 'none'}, 'requires': []}
        self.elf = bytearray(64)
        self.elf[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', self.elf, 18, 183)
        struct.pack_into('<H', self.elf, 54, 56)
        self.add('svc/console-raw', self.elf, '0755')
        self.add('etc/hyper/services.json', {
            'format': 'hyper.service-manifest', 'services': [
                {'name': 'console-raw', 'image': '/svc/console-raw'}],
        })

    def add(self, name, contents, mode='0644'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents if isinstance(contents, (bytes, bytearray))
                         else json.dumps(contents).encode())
        self.records.append([mode, name, str(path)])
        return path

    def add_profile(self, name, value=None):
        return self.add(f'{boot_profiles.DIRECTORY}/{name}.json', value or self.profile)

    def validate(self):
        with patch.object(boot_profiles.subprocess, 'run') as check:
            result = boot_profiles.validate_archive(self.records)
        return result, check

    def test_default_is_required_and_every_profile_reaches_runtime_checker(self):
        self.add_profile('default')
        self.add_profile('recovery')
        profiles, check = self.validate()
        self.assertEqual(set(profiles), {'default', 'recovery'})
        self.assertEqual(check.call_count, 2)
        for call in check.call_args_list:
            command = call.args[0]
            self.assertIn('--profile', command)
            self.assertIn('--bootstrap', command)
            self.assertIn('svc/console-raw', command)
        self.records = [record for record in self.records if not record[1].endswith('/default.json')]
        with self.assertRaisesRegex(ValueError, 'require default'):
            self.validate()

    def test_unselected_profile_missing_dependency_is_fatal(self):
        self.add_profile('default')
        alternate = copy.deepcopy(self.profile)
        alternate['requires'] = ['/svc/future-driver']
        self.add_profile('alternate', alternate)
        with self.assertRaisesRegex(ValueError, 'alternate.*future-driver.*absent from the boot archive'):
            self.validate()

    def test_formal_or_host_file_cannot_satisfy_boot_dependency(self):
        self.add_profile('default')
        self.records = [record for record in self.records if record[1] != 'svc/console-raw']
        # The host file remains, and a formal copy is packaged elsewhere.
        self.add('system/svc/console-raw', self.elf, '0755')
        with self.assertRaisesRegex(ValueError, 'console-raw.*absent from the boot archive'):
            self.validate()

    def test_required_files_resolve_archive_links_not_host_paths(self):
        self.profile['requires'] = ['/lib/driver.conf']
        self.add_profile('default')
        self.add('lib64/driver.conf', {'version': 1})
        self.records.append(['symlink', 'lib', 'lib64'])
        self.validate()
        self.records[-1][2] = 'not-packaged'
        with self.assertRaisesRegex(ValueError, 'driver.conf.*absent'):
            self.validate()

    def test_service_links_keep_the_runtime_lookup_path(self):
        self.add_profile('default')
        self.records[0][1] = 'svc/console-real'
        self.records.append(['symlink', 'svc/console-raw', 'console-real'])
        _, checker = self.validate()
        self.assertIn('svc/console-raw', checker.call_args.args[0])

    def test_service_execute_permission_and_architecture_match_entry(self):
        self.add_profile('default')
        self.records[0][0] = '0644'
        with self.assertRaisesRegex(ValueError, 'lacks executable permission'):
            self.validate()
        self.records[0][0] = '0755'
        self.add('init', self.elf, '0755')
        wrong = self.elf.copy()
        struct.pack_into('<H', wrong, 18, 243)
        (self.root / 'svc/console-raw').write_bytes(wrong)
        with self.assertRaisesRegex(ValueError, 'different architecture'):
            self.validate()

    def test_board_can_omit_io_vm_until_a_profile_requests_it(self):
        source = json.loads((ROOT / 'boards/qemu-minimal.json').read_text())
        del source['io-vm']
        board = Board.parse(source)
        with self.assertRaisesRegex(ValueError, 'requires explicit boot-profiles'):
            boot_profiles.needs_io_vm(None, board)
        product = system_config.ProductConfig({'version': 1, 'boot-profiles': {'default': self.profile}})
        self.assertFalse(boot_profiles.needs_io_vm(product, board))
        self.profile['io'] = {'kind': 'io-vm', 'service': 'io-runtime'}
        product = system_config.ProductConfig({'version': 1, 'boot-profiles': {'default': self.profile}})
        with self.assertRaisesRegex(ValueError, 'requires an I/O VM board'):
            boot_profiles.needs_io_vm(product, board)

    def test_provider_service_must_be_a_static_elf(self):
        self.add_profile('default')
        (self.root / 'svc/console-raw').write_bytes(b'not an executable')
        with self.assertRaisesRegex(ValueError, 'not an ELF'):
            self.validate()
        dynamic = self.elf + bytearray(56) + b'/lib64/ld-hyper-aarch64.so\0'
        struct.pack_into('<Q', dynamic, 32, 64)
        struct.pack_into('<H', dynamic, 56, 1)
        struct.pack_into('<IIQQQQQQ', dynamic, 64, 3, 0, 120, 0, 0, len(dynamic) - 120, 0, 1)
        (self.root / 'svc/console-raw').write_bytes(dynamic)
        with self.assertRaisesRegex(ValueError, 'must be a static ELF'):
            self.validate()

    def test_io_vm_requires_real_packaged_board_and_guest_image(self):
        self.profile['io'] = {'kind': 'io-vm', 'service': 'io-runtime'}
        self.add_profile('default')
        with self.assertRaisesRegex(ValueError, 'board.json.*absent'):
            self.validate()
        self.add('etc/hyper/board.json', {'io-vm': {'image': '/vm/io.itb'}})
        with self.assertRaisesRegex(ValueError, 'io.itb.*absent'):
            self.validate()
        self.add('vm/io.itb', b'fixture guest payload')
        self.validate()

    def test_generic_source_does_not_require_an_io_vm(self):
        self.profile['io'] = {'kind': 'service', 'service': 'block-driver'}
        self.add_profile('default')
        self.validate()
        product = system_config.ProductConfig({'version': 1, 'boot-profiles': {'default': self.profile}})
        self.assertFalse(boot_profiles.needs_io_vm(product))
        io = copy.deepcopy(self.profile)
        io['io']['kind'] = 'io-vm'
        product = system_config.ProductConfig({'version': 1, 'boot-profiles': {'default': self.profile, 'alternate': io}})
        self.assertTrue(boot_profiles.needs_io_vm(product))

    def test_generated_board_profile_declares_root_format_worker(self):
        board = Board.load(ROOT / 'boards/qemu.json')
        profile = boot_profiles.default_profile(services(board, ROOT), board=board)
        self.assertIn('/svc/fs-erofs', profile['requires'])
        self.assertEqual(profile['io'], {'kind': 'io-vm', 'service': 'io-runtime', 'successor': '/io-runtime'})

    def test_product_identity_includes_source_and_formal_successors(self):
        value = {'version': 1, 'boot-profiles': {'default': self.profile},
                 'system': {'storage-providers': []}}
        original = system_config.ProductConfig(value).identity()
        value['boot-profiles']['default']['requires'] = ['/svc/fs-fat']
        self.assertNotEqual(system_config.ProductConfig(value).identity(), original)
        value['system']['storage-providers'] = [{'component': '/block', 'image': '/svc/block'}]
        self.assertNotEqual(system_config.ProductConfig(value).identity(), original)

    def test_bad_profile_shapes_and_paths_fail_early(self):
        for value in ({}, {'alternate': self.profile}, {'../default': self.profile},
                      {'default': {**self.profile, 'typo': True}},
                      {'default': {**self.profile, 'services': '/etc/../services.json'}},
                      {'default': {**self.profile, 'requires': ['/driver', '/driver']}},
                      {'default': {**self.profile, 'io': {'kind': 'none', 'service': 'io-runtime'}}},
                      {'default': {**self.profile, 'io': {'kind': 'service', 'service': 'driver', 'successor': '/driver'}}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                boot_profiles.validate_set(value)

    def test_admission_failure_preserves_existing_archive_before_external_tools(self):
        self.profile['requires'] = ['/svc/missing']
        self.add_profile('default')
        output = self.root / 'bootstrap.cpio'
        output.write_bytes(b'previous published archive')
        manifest = self.root / 'entries.json'
        manifest.write_text(json.dumps(self.records))
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'scripts/pack-native-initramfs.py'),
            '--packer', '/no-such-packer', '--strip', '/no-such-strip',
            '--output', str(output), '--entries-from', str(manifest),
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('required file /svc/missing is absent', result.stderr)
        self.assertEqual(output.read_bytes(), b'previous published archive')

    def test_runtime_failure_fixture_changes_only_the_admitted_file(self):
        from board_bootstrap import newc
        spec = importlib.util.spec_from_file_location('cpio_fixture', ROOT / 'tests/qemu/cpio_fixture.py')
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        archive = self.root / 'fixture.cpio'
        archive.write_bytes(newc({'svc/provider': b'valid program', 'etc/policy': b'keep unchanged'}))
        fixture.replace_file(archive, 'svc/provider', b'broken')
        result = archive.read_bytes()
        self.assertIn(b'broken', result)
        self.assertNotIn(b'valid program', result)
        self.assertIn(b'keep unchanged', result)
        self.assertIn(b'TRAILER!!!', result)
        with self.assertRaisesRegex(ValueError, 'absent'):
            fixture.replace_file(archive, 'svc/missing', b'broken')
        self.assertEqual(archive.read_bytes(), result)


if __name__ == '__main__':
    unittest.main()
