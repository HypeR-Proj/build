#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Deployment parser, GPT integrity and non-destructive image publication tests."""

import binascii
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
from board_config import Board, SECTOR, GPT_TABLE_SECTORS, unique_object

spec = importlib.util.spec_from_file_location('pack_board', ROOT / 'scripts/pack-board-image.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((ROOT / 'boards/qemu.json').read_text())

    def network_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'network.json'
            subprocess.run([sys.executable, '-B', str(ROOT / 'tests/qemu/verify-network.py'),
                            'prepare', '--board', str(ROOT / 'boards/qemu.json'),
                            '--output', str(output)], check=True, stdout=subprocess.DEVNULL)
            return json.loads(output.read_text())

    def test_vm_policy_is_projected_without_image_mutation(self):
        original = Board.parse(copy.deepcopy(self.source))
        changed = copy.deepcopy(self.source)
        config = changed['virtual-machines'][0]['configuration']
        config.update({'vcpus': 4, 'memory-bytes': 128 * 1024 * 1024,
                       'bootargs': 'console=ttyAMA0 debug',
                       'affinity': [{'vcpu': 1, 'cpus': [0, 2, 64, 255]}]})
        board = Board.parse(changed)
        self.assertEqual(board.vms()['virtual-machines'][0]['configuration'], config)
        self.assertEqual(board.source['files'], original.source['files'])
        self.assertEqual(board.partitions, original.partitions)
        for field, value in [('vcpus', 0), ('vcpus', 9), ('memory-bytes', 123456789),
                             ('bootargs', 'a\0b'), ('platform', 'other')]:
            invalid = copy.deepcopy(changed)
            invalid['virtual-machines'][0]['configuration'][field] = value
            with self.assertRaises(ValueError):
                Board.parse(invalid)
        invalid = copy.deepcopy(changed)
        del invalid['virtual-machines'][0]['configuration']
        with self.assertRaises(ValueError):
            Board.parse(invalid)
        invalid = copy.deepcopy(changed)
        invalid['io-vm']['configuration']['memory-bytes'] = 64 * 1024 * 1024
        with self.assertRaises(ValueError):
            Board.parse(invalid)

    def test_affinity_validation_for_business_and_io_vms(self):
        for invalid in [None, {}, [{'vcpu': 0, 'cpus': []}],
                        [{'vcpu': 8, 'cpus': [0]}],
                        [{'vcpu': 0, 'cpus': [0, 0]}],
                        [{'vcpu': 0, 'cpus': [-1]}],
                        [{'vcpu': 0, 'cpus': [256]}],
                        [{'vcpu': 0, 'cpus': [True]}],
                        [{'vcpu': 0, 'cpus': [0], 'extra': 1}],
                        [{'vcpu': 0, 'cpus': [0]}, {'vcpu': 0, 'cpus': [1]}]]:
            for target in ('business', 'io'):
                with self.subTest(affinity=invalid, target=target):
                    changed = copy.deepcopy(self.source)
                    config = (changed['io-vm']['configuration'] if target == 'io' else
                              changed['virtual-machines'][0]['configuration'])
                    config['vcpus'] = 1 if target == 'io' else 2
                    config['affinity'] = invalid
                    with self.assertRaises(ValueError):
                        Board.parse(changed)

    def test_io_vm_has_one_complete_bootstrap_definition(self):
        for profile in ('qemu', 'rpi5', 'rpi5-native'):
            board = Board.load(ROOT / f'boards/{profile}.json')
            resident = board.source['io-vm']
            self.assertEqual(resident['runtime'], 'io-runtime')
            self.assertEqual(resident['configuration']['memory-bytes'], 128 * 1024 * 1024)
            self.assertEqual(resident['configuration']['vcpus'], 1)
            self.assertNotIn('io-device', board.source)
            self.assertNotIn(resident['name'], [vm['name'] for vm in board.vms()['virtual-machines']])
        for field, value in [('runtime', 'vm-runtime'), ('name', ''), ('name', '-io'),
                             ('name', 'x' * 33), ('name', 'alpine'), ('image', '/data/io.itb'),
                             ('image', '/vm/../io.itb'), ('image', '/vm//io.itb'),
                             ('image', '/vm/./io.itb'), ('image', '/vm/io.itb/'),
                             ('image', '/vm/io.elf'), ('image', '/vm/i\\o.itb'),
                             ('image', '/vm/i\x7fo.itb'), ('image', '/vm/' + 'x' * 505 + '.itb'),
                             ('image', '/vm/' + '\u00e9' * 253 + '.itb')]:
            invalid = copy.deepcopy(self.source)
            invalid['io-vm'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                Board.parse(invalid)
        for field in ('runtime', 'name', 'image', 'configuration', 'io-device'):
            invalid = copy.deepcopy(self.source)
            del invalid['io-vm'][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                Board.parse(invalid)
        for field, value in [('memory-bytes', 64 * 1024 * 1024), ('vcpus', 2)]:
            invalid = copy.deepcopy(self.source)
            invalid['io-vm']['configuration'][field] = value
            with self.assertRaisesRegex(ValueError, '128 MiB and one vCPU'):
                Board.parse(invalid)
        invalid = copy.deepcopy(self.source)
        invalid['io-device'] = invalid['io-vm']['io-device']
        with self.assertRaises(ValueError):
            Board.parse(invalid)
        valid = copy.deepcopy(self.source)
        valid['io-vm']['name'] = 'Storage_IO.1'
        valid['io-vm']['image'] = '/vm/' + 'x' * 504 + '.itb'
        self.assertEqual(len(valid['io-vm']['image']), 512)
        Board.parse(valid)

    def test_image_tool_prefers_path(self):
        with patch.object(packer.shutil, 'which', return_value='/tools/mkfs.fat'), \
                patch.object(packer.subprocess, 'run') as run:
            self.assertEqual(packer.image_tool('mkfs.fat', 'mkfs.fat', 'dosfstools'),
                             '/tools/mkfs.fat')
            run.assert_not_called()

    def test_image_tool_discovers_brew_sbin(self):
        with tempfile.TemporaryDirectory() as tmp:
            brew = Path(tmp) / "brew"
            sbin = Path(tmp) / "sbin"
            executable = sbin / "mkfs.fat"

            sbin.mkdir()
            brew.touch()
            executable.touch()

            executable.chmod(0o755)

            with patch.object(
                packer.shutil, "which",
                side_effect=lambda name: str(brew) if name == "brew" else None,
            ), patch.object(
                packer.Path, "is_file", autospec=True,
                side_effect=lambda path: path == executable,
            ), patch.object(packer.subprocess, "run") as run:
                run.return_value.stdout = f"{tmp}\n"

                self.assertEqual(
                    packer.image_tool("mkfs.fat", "mkfs.fat", "dosfstools"),
                    str(executable),
                )

                self.assertEqual(
                    run.call_args.args[0],
                    [str(brew), "--prefix", "dosfstools"],
                )

    def test_image_tool_prefers_linux_sbin_to_brew(self):
        executable = Path('/usr/sbin/mkfs.fat')
        with patch.object(packer.shutil, 'which',
                          side_effect=lambda name: '/tools/brew' if name == 'brew' else None), \
                patch.object(packer.Path, 'is_file', autospec=True,
                             side_effect=lambda path: path == executable), \
                patch.object(packer.os, 'access', return_value=True), \
                patch.object(packer.subprocess, 'run') as run:
            self.assertEqual(packer.image_tool('mkfs.fat', 'mkfs.fat', 'dosfstools'),
                             str(executable))
            run.assert_not_called()

    def test_missing_explicit_tool_does_not_fall_back(self):
        with patch.object(packer.shutil, 'which', return_value=None), \
                patch.object(packer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'was not found'):
                packer.image_tool('/missing/mkfs.fat', 'mkfs.fat', 'dosfstools')
            run.assert_not_called()

    def test_build_defaults_follow_custom_board_and_explicit_inputs_stay_strict(self):
        source = copy.deepcopy(self.source)
        source['files']['vm/alpine.itb'] = 'custom'
        board = Board.parse(source)
        defaults = ['hyper=kernel', 'bootstrap=initramfs', 'alpine=unused', 'alpine-rootfs=root.ext4']
        actual = packer.artifact_inputs(board, defaults, ['custom=my-guest', 'alpine-rootfs=my-rootfs'])
        self.assertNotIn('alpine', actual)
        self.assertEqual(actual['custom'], Path('my-guest'))
        self.assertNotIn('hyper', actual)
        self.assertEqual(actual['alpine-rootfs'], Path('my-rootfs'))
        for explicit in ([], ['custom=x', 'typo=y'], ['custom=x', 'custom=y']):
            with self.assertRaises(ValueError):
                packer.artifact_inputs(board, defaults, explicit)

    def test_board_profiles_have_same_abi_and_explicit_volumes(self):
        for profile in ('qemu', 'rpi5'):
            board = Board.load(ROOT / f'boards/{profile}.json')
            self.assertEqual(board.source['architecture'], 'aarch64')
            self.assertEqual(board.partitions[0].sectors * SECTOR, 1024**3)
            self.assertEqual(board.partitions[0].content, 'data')
            self.assertEqual(board.vms()['virtual-machines'][0]['image'], '/data/vm/alpine.itb')
            self.assertEqual(board.partitions[1].start, board.partitions[0].end + 1)
            self.assertNotEqual(board.partitions[0].identifier, board.partitions[1].identifier)

    def test_configuration_payload_does_not_duplicate_io_vm(self):
        for profile in ('qemu', 'rpi5'):
            board = Board.load(ROOT / f'boards/{profile}.json')
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                artifacts = {}
                for key in board.source['files'].values():
                    artifacts[key] = root / key
                    artifacts[key].write_bytes(key.encode())
                    if key == 'hyper' and profile == 'rpi5':
                        header = bytearray(64)
                        header[16:24] = (4096).to_bytes(8, 'little')
                        header[56:60] = b'ARM\x64'
                        artifacts[key].write_bytes(header)
                for part in board.partitions:
                    if part.image:
                        artifacts[part.image] = root / part.image
                        with artifacts[part.image].open('wb') as disk:
                            disk.truncate(part.sectors * SECTOR)
                payload = root / 'payload'
                payload.mkdir()
                packer.prepare_payload(board, artifacts, payload)
                self.assertFalse((payload / 'vm/io.itb').exists())
                self.assertEqual((payload / 'hyper.img').is_file(), profile == 'rpi5')
                self.assertEqual((payload / 'bootstrap.cpio').is_file(), profile == 'rpi5')
                self.assertTrue((payload / 'vm/alpine.itb').is_file())

    def test_system_payload_is_separate_and_vm_paths_remain_data(self):
        self.source['disk']['partitions'][2].pop('image')
        board = Board.parse(self.source)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / 'alpine.itb'
            artifact.write_bytes(b'guest')
            system = root / 'system'
            (system / 'bin').mkdir(parents=True)
            (system / 'bin/tool').write_bytes(b'native program')
            payload = root / 'payload'
            payload.mkdir()
            packer.prepare_payload(board, {'alpine': artifact}, payload, system)
            self.assertFalse((payload / 'bin/tool').exists())
            vms = json.loads((payload / 'vms.json').read_bytes())
            self.assertEqual(vms['virtual-machines'][0]['image'], '/data/vm/alpine.itb')
            # Record a link itself; never hash the link's host target.
            (system / 'bin/linked').symlink_to(artifact)
            self.assertEqual(packer.system_files(system)['bin/linked'],
                             {'symlink': str(artifact)})

    def test_system_payload_does_not_replace_data_configuration(self):
        self.source['virtual-machines'] = []
        self.source['disk']['partitions'][2].pop('image')
        board = Board.parse(self.source)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / 'alpine.itb'
            artifact.write_bytes(b'guest')
            system = root / 'system'
            system.mkdir()
            (system / 'board.json').write_text('{}')
            payload = root / 'payload'
            payload.mkdir()
            packer.prepare_payload(board, {'alpine': artifact}, payload, system)
            self.assertEqual(json.loads((payload / 'board.json').read_text()), board.source)

    def test_stale_system_payload_requires_explicit_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'disk.img'
            output.write_bytes(b'persistent user data')
            system = root / 'system'
            system.mkdir()
            tool = system / 'tool'
            tool.write_bytes(b'build one')
            with self.assertRaisesRegex(ValueError, 'no system payload record'):
                packer.check_system_payload(output, system)
            packer.system_state_path(output).write_text(json.dumps(packer.system_state(system)))
            packer.check_system_payload(output, system)
            tool.write_bytes(b'build two')
            with self.assertRaisesRegex(ValueError, 'board-rebuild'):
                packer.check_system_payload(output, system)
            self.assertEqual(output.read_bytes(), b'persistent user data')

    def test_native_volumes_reserve_disjoint_clients_and_gpt_partitions(self):
        board = Board.parse(self.source)
        self.assertEqual([p.name for p in board.partitions], ['hyper-data', 'hyper-system', 'alpine'])
        self.assertEqual(board.native_volumes(), [
            {'client': 8, 'source': 'PARTLABEL=hyper-data', 'read_only': False},
            {'client': 7, 'source': 'PARTLABEL=hyper-system', 'read_only': True}])
        self.assertEqual(board.backend_clients().splitlines()[-2:], ['8 - -', '7 - -'])
        self.assertIn('1 alpine ', board.bootstrap_clients())
        self.assertTrue(board.source['native']['boot-fstab'].startswith('PARTLABEL=hyper-system /system'))
        self.assertNotIn('/data', board.source['native']['boot-fstab'])
        self.assertEqual(board.source['native']['fstab'], 'PARTLABEL=hyper-data /data auto rw 0 0\n')
        for first, second in zip(board.partitions, board.partitions[1:]):
            self.assertLess(first.end, second.start)

    def test_all_ramfs_has_no_system_disk_or_worker_build_requirement(self):
        board = Board.parse(self.source, layout='ramfs')
        self.assertEqual([p.name for p in board.partitions], ['hyper-data', 'alpine'])
        self.assertEqual(len(board.native_volumes()), 1)
        board.source['native']['fstab'] = 'PARTLABEL=hyper-data /data erofs ro 0 0\n'
        Board.parse(board.source)  # Runtime magic/worker selection owns mount errors.

    def test_omitted_native_policy_is_canonical_split_without_mutating_source(self):
        original = copy.deepcopy(self.source)
        board = Board.parse(self.source)
        explicit = copy.deepcopy(self.source)
        explicit['native'] = {
            'layout': 'split',
            'volumes': [{'source': 'PARTLABEL=hyper-data', 'read-only': False},
                        {'source': 'PARTLABEL=hyper-system', 'read-only': True}],
            'boot-fstab': 'PARTLABEL=hyper-system /system auto ro 0 0\n',
            'fstab': 'PARTLABEL=hyper-data /data auto rw 0 0\n',
        }
        self.assertEqual(self.source, original)
        self.assertEqual(board.source, explicit)
        self.assertEqual(board, Board.parse(explicit))
        self.assertEqual(board, Board.parse(board.source))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'board.json'
            path.write_text(json.dumps(self.source))
            self.assertEqual(board, Board.load(path))
            path.write_text(json.dumps(board.source))
            self.assertEqual(board, Board.load(path))

    def test_layout_selection_changes_only_native_system_medium(self):
        disk = Board.parse(self.source)
        ramfs = Board.parse(self.source, layout='ramfs')
        self.assertEqual(ramfs.source['native'], {
            'layout': 'ramfs', 'volumes': [{'source': 'PARTLABEL=hyper-data', 'read-only': False}],
            'boot-fstab': '',
            'fstab': 'PARTLABEL=hyper-data /data auto rw 0 0\n'})
        self.assertEqual(disk.vms(), ramfs.vms())
        self.assertEqual(disk.source['files'], ramfs.source['files'])
        self.assertEqual(disk.source['io-vm'], ramfs.source['io-vm'])
        self.assertEqual(disk.partitions[0], ramfs.partitions[0])
        self.assertEqual(Board.parse(ramfs.source), ramfs)
        for layout, source in [('ramfs', disk.source), ('split', ramfs.source)]:
            with self.subTest(layout=layout), self.assertRaisesRegex(ValueError, 'conflicts'):
                Board.parse(source, layout=layout)

    def test_removed_flat_and_null_native_policies_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'split or ramfs'):
            Board.parse(self.source, layout='flat')
        for policy in (None, {'layout': 'flat', 'volumes': [], 'fstab': ''}):
            source = copy.deepcopy(self.source)
            source['native'] = policy
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                Board.parse(source)

    def test_prepare_cli_preserves_one_policy_for_all_consumers(self):
        from board_bootstrap import stage
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.json'
            source.write_text(json.dumps(self.source))
            original = source.read_bytes()
            for layout in (None, 'split', 'ramfs'):
                with self.subTest(layout=layout):
                    output = root / f'{layout}.json'
                    command = [sys.executable, '-B', str(ROOT / 'scripts/prepare-native-layout.py'),
                               '--board', str(source), '--output', str(output)]
                    if layout is not None:
                        command.extend(['--layout', layout])
                    subprocess.run(command, check=True, capture_output=True)
                    board = Board.load(output)
                    self.assertEqual(board, Board.load(source, layout=layout))
                    staged = root / str(layout)
                    stage(board, ROOT, staged)
                    for name in ('boot-fstab', 'fstab'):
                        self.assertEqual((staged / name).read_text(), board.source['native'][name])
                    self.assertEqual(json.loads((staged / 'storage.json').read_text()),
                                     board.storage_policy())
                    self.assertEqual((staged / 'io-clients.conf').read_text(),
                                     board.bootstrap_clients())
                    stamp = output.stat().st_mtime_ns
                    subprocess.run(command, check=True, capture_output=True)
                    self.assertEqual(output.stat().st_mtime_ns, stamp)
            self.assertEqual(source.read_bytes(), original)

    def test_system_native_slot_cannot_overlap_guest_clients(self):
        source = copy.deepcopy(self.source)
        prototype = source['virtual-machines'][0]
        prototype.pop('network')
        source['virtual-machines'] = [dict(prototype, name=f'guest{i}') for i in range(7)]
        with self.assertRaisesRegex(ValueError, 'eight I/O data slots'):
            Board.parse(source)
        ramfs = Board.parse(source, layout='ramfs')
        self.assertIn('7 guest6 - -\n', ramfs.bootstrap_clients())
        source['virtual-machines'].pop()
        split = Board.parse(source)
        clients = [row.split()[0] for row in split.backend_clients().splitlines()[1:]]
        self.assertEqual(sorted(map(int, clients)), list(range(9)))

    def test_pi_image_reserves_noload_extent_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'hyper.img'
            header = bytearray(64)
            header[16:24] = (8192).to_bytes(8, 'little')
            header[56:60] = b'ARM\x64'
            image.write_bytes(header)
            packer.pad_arm64_boot_image(image)
            self.assertEqual(image.stat().st_size, 8192)
            self.assertEqual(image.read_bytes()[:64], header)
            self.assertEqual(image.read_bytes()[64:], bytes(8192 - 64))
            image.write_bytes(b'not an Image')
            with self.assertRaisesRegex(ValueError, 'Image header'):
                packer.pad_arm64_boot_image(image)

    def test_rpi5_firmware_requires_bootstrap_archive(self):
        source = json.loads((ROOT / 'boards/rpi5.json').read_text())
        del source['files']['bootstrap.cpio']
        with self.assertRaisesRegex(ValueError, 'missing boot profile payloads'):
            Board.parse(source)

    def test_assignment_selector_is_explicit_and_unambiguous(self):
        for selector in ({'profile': 'virtio-mmio-scsi'},
                         {'profile': 'virtio-mmio-scsi', 'compatible': 'virtio,mmio', 'path': '/soc/device'},
                         {'profile': 'unknown', 'compatible': 'virtio,mmio'},
                         {'profile': 'virtio-mmio-scsi', 'path': '/soc/../device'},
                         {'profile': 'virtio-mmio-scsi', 'compatible': ''}):
            source = copy.deepcopy(self.source)
            source['io-vm']['io-device'] = selector
            with self.assertRaises(ValueError):
                Board.parse(source)
        source = copy.deepcopy(self.source)
        source['io-vm']['io-device'] = {'profile': 'virtio-mmio-scsi', 'path': '/soc/virtio@a000000'}
        Board.parse(source)
        source['virtual-machines'] = [dict(source['virtual-machines'][0], name=f'vm{i}') for i in range(9)]
        for vm in source['virtual-machines']:
            vm.pop('network')
        with self.assertRaises(ValueError):
            Board.parse(source)

    def test_bootstrap_projection_keeps_partition_policy_out_of_linux(self):
        source = copy.deepcopy(self.source)
        for field in ('network-device', 'networks'):
            del source['io-vm'][field]
        del source['virtual-machines'][0]['network']
        board = Board.parse(source)
        self.assertEqual(board.bootstrap_clients(),
                         'hyper.clients.v3\n0 - - -\n1 alpine - -\n')
        self.assertEqual(board.backend_clients(),
                         'hyper.clients.v3\n0 - -\n1 - -\n8 - -\n7 - -\n')
        self.assertEqual(board.bootstrap_networks(), 'hyper.networks.v1\n')
        self.assertEqual(board.vms()['virtual-machines'][0]['disk'],
                         {'client': 1, 'volume': 'alpine'})
        self.assertEqual(board.storage_policy()['guests'],
                         [{'client': 1, 'source': 'PARTLABEL=alpine', 'read_only': False}])
        for emitted in (board.backend_clients(), board.bootstrap_networks()):
            self.assertNotIn('PARTLABEL', emitted)
            for partition in board.partitions:
                self.assertNotIn(str(partition.identifier), emitted)

    def test_network_projection_shares_client_and_preserves_native_reservation(self):
        self.assertEqual(len(Board.load(ROOT / 'boards/qemu.json').vms()['virtual-machines']), 1)
        board = Board.parse(self.network_fixture())
        definitions = board.vms()['virtual-machines']
        self.assertEqual(definitions[0]['disk']['client'], definitions[0]['network']['client'])
        self.assertEqual(definitions[0]['network'],
                         {'client': 1, 'network': 'default', 'mac': '02:48:59:00:00:01'})
        self.assertNotIn('disk', definitions[1])
        self.assertEqual(definitions[1]['network']['client'], 2)
        self.assertEqual([part.name for part in board.partitions], ['hyper-data', 'hyper-system', 'alpine'])
        self.assertEqual(board.bootstrap_clients(),
                         'hyper.clients.v3\n0 - - -\n'
                         '1 alpine default 02:48:59:00:00:01\n'
                         '2 - default 02:48:59:00:00:02\n')
        self.assertEqual(board.bootstrap_networks(), 'hyper.networks.v1\ndefault hbr0 eth0\n')

    def test_client_numbers_do_not_follow_disk_partition_numbers(self):
        source = self.network_fixture()
        source['virtual-machines'].reverse()
        isolated = copy.deepcopy(source['virtual-machines'][0])
        isolated['name'] = 'isolated'
        del isolated['network']
        source['virtual-machines'].insert(1, isolated)
        board = Board.parse(source)
        self.assertEqual([part.name for part in board.partitions], ['hyper-data', 'hyper-system', 'alpine'])
        self.assertEqual(board.vms()['virtual-machines'][2]['disk']['client'], 3)
        self.assertEqual(board.vms()['virtual-machines'][2]['network']['client'], 3)
        self.assertEqual(board.bootstrap_clients(),
                         'hyper.clients.v3\n0 - - -\n'
                         '1 - default 02:48:59:00:00:02\n'
                         '3 alpine default 02:48:59:00:00:01\n')

    def test_rpi5_network_assigns_whole_pci_function_and_shared_client_contract(self):
        board = Board.load(ROOT / 'boards/rpi5.json')
        qemu = Board.load(ROOT / 'boards/qemu.json')
        self.assertEqual(board.source['io-vm']['network-device'],
                         {'profile': 'pci-function', 'pci-id': '1de4:0001'})
        self.assertEqual(board.source['io-vm']['io-device']['profile'], 'bcm2712-sdhci')
        self.assertEqual(board.bootstrap_clients(), qemu.bootstrap_clients())
        self.assertEqual(board.bootstrap_networks(), qemu.bootstrap_networks())
        self.assertEqual(board.vms(), qemu.vms())
        self.assertEqual(len(board.source['virtual-machines']), 1)

        for source, controller in ((board.source, qemu.source['io-vm']['network-device']),
                                   (qemu.source, board.source['io-vm']['network-device'])):
            invalid = copy.deepcopy(source)
            invalid['io-vm']['network-device'] = controller
            with self.assertRaisesRegex(ValueError, 'does not match'):
                Board.parse(invalid)

    def test_pci_identity_is_canonical_and_profile_specific(self):
        source = json.loads((ROOT / 'boards/rpi5.json').read_text())
        for selector in (
            {'profile': 'pci-function', 'pci-id': None},
            {'profile': 'pci-function', 'pci-id': '1DE4:0001'},
            {'profile': 'pci-function', 'pci-id': '1de4:1'},
            {'profile': 'pci-function', 'pci-id': '1de4-0001'},
            {'profile': 'pci-function', 'compatible': 'pci1de4,1'},
            {'profile': 'pci-function', 'path': '/pcie/rp1'},
            {'profile': 'pci-function', 'pci-id': '1de4:0001', 'path': '/pcie/rp1'},
            {'profile': 'virtio-mmio-net', 'pci-id': '1de4:0001'},
        ):
            invalid = copy.deepcopy(source)
            invalid['io-vm']['network-device'] = selector
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                Board.parse(invalid)

    def test_network_policy_is_explicit_and_bounded(self):
        source = json.loads((ROOT / 'boards/qemu.json').read_text())
        cases = []
        for field in ('networks', 'network-device'):
            invalid = copy.deepcopy(source)
            del invalid['io-vm'][field]
            cases.append(invalid)
        for networks in (None, {}, [], source['io-vm']['networks'] * 2,
                         [{'name': 'default', 'bridge': 'same', 'uplink': 'same'}],
                         [{'name': 'default', 'bridge': 'hbr0', 'uplink': '../eth0'}],
                         [{'name': 'default', 'bridge': 'x' * 16, 'uplink': 'eth0'}],
                         [{'name': 'default', 'bridge': 'hbr0', 'uplink': 'eth0', 'mtu': 9000}]):
            invalid = copy.deepcopy(source)
            invalid['io-vm']['networks'] = networks
            cases.append(invalid)
        invalid = copy.deepcopy(source)
        invalid['io-vm']['network-device']['profile'] = 'virtio-mmio-scsi'
        cases.append(invalid)
        invalid = json.loads((ROOT / 'boards/rpi5.json').read_text())
        invalid['io-vm'].update({field: source['io-vm'][field]
                                for field in ('network-device', 'networks')})
        cases.append(invalid)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                Board.parse(case)

    def test_guest_network_rejects_unknown_network_invalid_mac_and_split_client(self):
        source = self.network_fixture()
        cases = [{'network': 'unknown', 'mac': '02:48:59:00:00:01'}, None,
                 {'network': 'default', 'mac': '02:48:59:00:00:01', 'client': 2}]
        for mac in ('00:48:59:00:00:01', '03:48:59:00:00:01', 'ff:ff:ff:ff:ff:ff',
                    '02:48:59:00:00:AA', '02:48:59:00:00', '02:48:59:00:00:01\n', None):
            cases.append({'network': 'default', 'mac': mac})
        for network in cases:
            invalid = copy.deepcopy(source)
            invalid['virtual-machines'][0]['network'] = network
            with self.subTest(network=network), self.assertRaises(ValueError):
                Board.parse(invalid)
        source['virtual-machines'][1]['network'] = source['virtual-machines'][0]['network']
        with self.assertRaisesRegex(ValueError, 'MAC addresses must be unique'):
            Board.parse(source)
        source = copy.deepcopy(self.source)
        del source['io-vm']['network-device']
        del source['io-vm']['networks']
        with self.assertRaisesRegex(ValueError, 'configured I/O VM network'):
            Board.parse(source)

    def test_disk_image_requires_an_explicit_partition_size(self):
        source = copy.deepcopy(self.source)
        del source['disk']['partitions'][2]['size-mib']
        with self.assertRaisesRegex(ValueError, 'invalid partition fields'):
            Board.parse(source)
        for disk in ({'source': 'alpine'}, {'source': 'PARTLABEL=alpine', 'read-only': 1},
                     {'source': 'PARTLABEL=alpine', 'client': 1}, None):
            source = copy.deepcopy(self.source)
            source['virtual-machines'][0]['disk'] = disk
            with self.subTest(disk=disk), self.assertRaises(ValueError):
                Board.parse(source)

    def test_linux_overlay_keeps_network_and_client_policy_together(self):
        import board_bootstrap
        board = Board.load(ROOT / 'boards/qemu.json')
        with patch.object(board_bootstrap, 'newc', return_value=b'archive') as archive:
            original = b'verified linux archive'
            overlay = board_bootstrap.linux_overlay(board, original)
        self.assertTrue(overlay.startswith(original))
        self.assertEqual(archive.call_args.args[0], {
            'etc/hyper-clients.conf': board.backend_clients().encode(),
            'etc/hyper-networks.conf': board.bootstrap_networks().encode(),
        })

    def test_gpt_headers_and_backup_agree(self):
        board = Board.parse(self.source)
        blocks = dict(packer.gpt(board))
        self.assertEqual(blocks[0][510:], b'\x55\xaa')
        first, last = blocks[1], blocks[board.disk_sectors - 1]
        self.assertEqual(first[:8], b'EFI PART')
        for header in (first, last):
            check = bytearray(header[:92])
            expected = struct.unpack_from('<I', check, 16)[0]
            struct.pack_into('<I', check, 16, 0)
            self.assertEqual(binascii.crc32(check), expected)
        self.assertEqual(struct.unpack_from('<Q', first, 32)[0], board.disk_sectors - 1)
        self.assertEqual(struct.unpack_from('<Q', last, 32)[0], 1)
        table = blocks[2]
        self.assertEqual(blocks[board.disk_sectors - 1 - GPT_TABLE_SECTORS], table)
        self.assertEqual(struct.unpack_from('<I', first, 88)[0], binascii.crc32(table))
        last_usable = struct.unpack_from('<Q', first, 48)[0]
        self.assertLessEqual(board.partitions[-1].end, last_usable)
        for index, partition in enumerate(board.partitions):
            entry = struct.unpack_from('<16s16sQQQ72s', table, index * 128)
            self.assertEqual(entry[1], partition.identifier.bytes_le)
            self.assertEqual(entry[2:4], (partition.start, partition.end))

    def test_rejects_untrusted_configuration(self):
        cases = []
        for key, value in [('format', 'hyper.board.v2'), ('architecture', 'riscv64'),
                           ('board', '../bad'), ('boot', 'guess'), ('extra', 1)]:
            case = copy.deepcopy(self.source)
            case[key] = value
            cases.append(case)
        for size in (True, 0, -1, 2**64, '1024'):
            case = copy.deepcopy(self.source)
            case['disk']['partitions'][0]['size-mib'] = size
            cases.append(case)
        for path in ('../escape', '/absolute', 'vm/../bad', 'VM/ALPINE.ITB',
                     'vm', 'partitions.json', 'config.txt', 'x\\y', 'x/./y'):
            case = copy.deepcopy(self.source)
            case['files'][path] = 'artifact'
            cases.append(case)
        case = copy.deepcopy(self.source)
        case['virtual-machines'] *= 2
        cases.append(case)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                Board.parse(case)

    def test_data_label_and_guest_name_are_configuration_not_reserved_roles(self):
        source = copy.deepcopy(self.source)
        source['disk']['partitions'][0]['label'] = '持久化 data'
        source['virtual-machines'][0]['name'] = 'config'
        board = Board.parse(source)
        self.assertEqual(board.partitions[0].name, '持久化 data')
        self.assertIn('PARTLABEL=持久化\\040data /data auto rw', board.source['native']['fstab'])
        self.assertEqual(board.native_volumes()[0]['source'], 'PARTLABEL=持久化 data')
        self.assertEqual(board.vms()['virtual-machines'][0]['name'], 'config')
        # Renaming the VM does not choose another backing partition.
        self.assertEqual(board.storage_policy()['guests'][0]['source'], 'PARTLABEL=alpine')

    def test_explicit_partition_uuid_survives_label_and_order_changes(self):
        source = copy.deepcopy(self.source)
        identifier = '72362b66-d96e-4f15-8bd3-c292a2907a94'
        source['disk']['partitions'][0]['uuid'] = identifier
        board = Board.parse(source)
        source['disk']['partitions'][0]['label'] = 'my persistent data'
        source['disk']['partitions'].reverse()
        renamed = Board.parse(source)
        self.assertEqual(str(board.partitions[0].identifier), identifier)
        self.assertEqual(str(renamed.partitions[-1].identifier), identifier)
        self.assertEqual(renamed.partitions[-1].content, 'data')
        self.assertNotEqual(board.partitions[0].start, renamed.partitions[-1].start)

    def test_partition_definitions_reject_duplicate_identity_and_conflicting_content(self):
        cases = []
        for field, value in [('label', ''), ('label', 'x' * 37), ('label', 'bad\x00label'),
                             ('uuid', '00000000-0000-0000-0000-000000000000'),
                             ('filesystem', 'unsupported'), ('content', 'other'),
                             ('image', '../escape')]:
            source = copy.deepcopy(self.source)
            source['disk']['partitions'][0][field] = value
            cases.append(source)
        for duplicate in ('label', 'uuid', 'content'):
            source = copy.deepcopy(self.source)
            if duplicate == 'uuid':
                source['disk']['partitions'][0]['uuid'] = '72362b66-d96e-4f15-8bd3-c292a2907a94'
            source['disk']['partitions'][1][duplicate] = source['disk']['partitions'][0][duplicate]
            cases.append(source)
        source = copy.deepcopy(self.source)
        source['disk']['partitions'][0]['image'] = 'conflicts-with-generated-content'
        cases.append(source)
        for source in cases:
            with self.subTest(partitions=source['disk']['partitions']), self.assertRaises(ValueError):
                Board.parse(source)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"disk": 1, "disk": 2}', object_pairs_hook=unique_object)

    def test_existing_output_never_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'disk.img'
            output.write_bytes(b'user disk')
            with self.assertRaises(ValueError):
                packer.build(Board.parse(self.source), {}, output)
            self.assertEqual(output.read_bytes(), b'user disk')

    def test_sparse_copy_bounded_and_preserves_neighbor(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.write_bytes(b'X' * 512)
            target = Path(directory) / 'target'
            target.write_bytes(b'A' * 1536)
            with target.open('r+b') as destination:
                packer.sparse_copy(source, destination, 512, 512)
            self.assertEqual(target.read_bytes(), b'A' * 512 + b'X' * 512 + b'A' * 512)
            with target.open('r+b') as destination, self.assertRaises(ValueError):
                packer.sparse_copy(source, destination, 0, 511)

    def test_real_fat_image_and_guest_container(self):
        import subprocess
        try:
            packer.image_tool('mkfs.fat', 'mkfs.fat', 'dosfstools')
            mcopy = packer.image_tool('mcopy', 'mcopy', 'mtools')
        except ValueError as error:
            self.skipTest(str(error))
        self.source['disk']['partitions'][0]['size-mib'] = 64
        self.source['disk']['partitions'][2]['size-mib'] = 8
        self.source['disk']['partitions'][2]['image'] = 'guest-disk'
        # This fixture qualifies FAT and opaque guest disk publication only.
        board = Board.parse(self.source, layout='ramfs')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            artifacts = {}
            for key in self.source['files'].values():
                artifacts[key] = directory / key
                artifacts[key].write_bytes(key.encode())
            disk = directory / 'guest'
            with disk.open('wb') as stream:
                stream.truncate(8 * 1024**2)
                stream.write(b'guest-sector-zero')
            artifacts['guest-disk'] = disk
            output = directory / 'board.img'
            output.write_bytes(b'old user disk')
            with patch.object(packer, 'prepare_payload', side_effect=ValueError('bad input')):
                with self.assertRaises(ValueError):
                    packer.build(board, artifacts, output, replace=True)
            self.assertEqual(output.read_bytes(), b'old user disk')
            packer.build(board, artifacts, output, replace=True)
            config_file = directory / 'partitions.json'
            subprocess.run([mcopy, '-i', f'{output}@@{board.partitions[0].start * SECTOR}',
                            '::/partitions.json', str(config_file)], check=True)
            self.assertEqual(json.loads(config_file.read_bytes()), board.partition_manifest())
            with output.open('rb') as stream:
                stream.seek(board.partitions[1].start * SECTOR)
                self.assertEqual(stream.read(17), b'guest-sector-zero')
            self.assertEqual(disk.stat().st_size, 8 * 1024**2)

    def test_real_erofs_system_image_is_reproducible(self):
        # Exercise the installed tool, including CI's erofs-utils 1.7, rather
        # than mocking its command line and accepting unsupported options.
        try:
            mkfs = packer.image_tool('mkfs.erofs', 'mkfs.erofs', 'erofs-utils')
        except ValueError as error:
            self.skipTest(str(error))
        board = Board.parse(self.source)
        system = next(part for part in board.partitions if part.content == 'system')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            root = directory / 'root'
            root.mkdir()
            (root / 'hello').write_bytes(b'system file\n')
            (root / 'link').symlink_to('hello')
            contents = []
            for name in ('first', 'second'):
                staging = directory / name
                staging.mkdir()
                images = packer.prepare_native_volumes(board, staging, root, None, None, mkfs)
                self.assertEqual(set(images), {system.name})
                contents.append(images[system.name].read_bytes())
            self.assertEqual(struct.unpack_from('<I', contents[0], 1024)[0], 0xe0f5e1e2)
            self.assertEqual(contents[0], contents[1])


if __name__ == '__main__':
    unittest.main()
