#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Protect persistent disks used by interactive I/O VM boots."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import sys
import json
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

spec = importlib.util.spec_from_file_location(
    'io_runner', Path(__file__).resolve().parents[2] / 'scripts/run-io-vm.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
spec = importlib.util.spec_from_file_location(
    'io_images', Path(__file__).resolve().parents[2] / 'scripts/io-vm-images.py')
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


class DiskTests(unittest.TestCase):
    def test_board_without_io_vm_has_no_vm_network_or_appliance(self):
        from board_config import Board
        from board_bootstrap import services
        root = Path(__file__).resolve().parents[2]
        source = json.loads((root / 'boards/qemu-minimal.json').read_text())
        del source['io-vm']
        board = Board.parse(source)
        self.assertEqual(runner.network_arguments(board), [])
        self.assertEqual(runner.storage_controller(board), runner.storage_controller(None))
        self.assertEqual(runner.machine_arguments(board, 'virt'), 'virt')
        self.assertEqual(board.bootstrap_networks(), 'hyper.networks.v1\n')
        graph = services(board, root)
        self.assertNotIn('io-runtime', [service['name'] for service in graph['services']])
        with self.assertRaisesRegex(ValueError, 'requires an I/O VM board'):
            images.deployment_board(board, 'storage')

    def test_guest_backend_without_io_vm_is_rejected_explicitly(self):
        from board_config import Board
        root = Path(__file__).resolve().parents[2]
        source = json.loads((root / 'boards/qemu.json').read_text())
        del source['io-vm']
        with self.assertRaisesRegex(ValueError, 'guest disk/network backend requires an I/O VM'):
            Board.parse(source)

    def test_standby_device_matches_boardless_mmio_launcher(self):
        from board_config import Board
        root = Path(__file__).resolve().parents[2]
        board = Board.load(root / 'boards/qemu.json')
        standby = images.deployment_board(board, 'standby')
        self.assertEqual(standby.source['io-vm']['io-device'], {
            'profile': 'virtio-mmio-scsi', 'compatible': 'virtio,mmio',
        })
        self.assertEqual(runner.storage_controller(standby), runner.storage_controller(None))
        self.assertEqual(runner.network_arguments(standby), runner.network_arguments(None))
        self.assertEqual(runner.machine_arguments(standby, 'virt'), 'virt')
        self.assertEqual(board.source['io-vm']['io-device']['profile'], 'pci-function')

    def test_standby_override_does_not_change_production_or_pi_storage(self):
        from board_config import Board
        root = Path(__file__).resolve().parents[2]
        for name, mode in (('qemu.json', 'storage'), ('rpi5-native.json', 'standby')):
            with self.subTest(board=name, mode=mode):
                board = Board.load(root / 'boards' / name)
                deployed = images.deployment_board(board, mode)
                self.assertEqual(deployed.source['io-vm']['io-device'],
                                 board.source['io-vm']['io-device'])

    def test_network_uplink_requires_explicit_board_policy(self):
        from board_config import Board
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(runner.network_arguments(None), [])
        source = json.loads((root / 'boards/qemu.json').read_text())
        del source['io-vm']['network-device']
        del source['io-vm']['networks']
        del source['virtual-machines'][0]['network']
        self.assertEqual(runner.network_arguments(Board.parse(source)), [])
        self.assertEqual(runner.network_arguments(Board.load(root / 'boards/qemu.json')),
                         ['-netdev', 'user,id=physicalnet', '-device',
                          'virtio-net-pci,addr=2.0,disable-legacy=on,romfile=,id=physicalnet,netdev=physicalnet,iommu_platform=on'])

    def test_board_network_is_included_in_the_executed_command(self):
        from board_config import Board
        board = Board.load(Path(__file__).resolve().parents[2] / 'boards/qemu.json')
        with patch.object(sys, 'argv', ['run-io-vm.py', '--qemu', 'qemu',
                          '--image', 'kernel.img', '--initramfs', 'init.cpio',
                          '--disk', 'disk.img', '--board', 'board.json']), \
                patch.object(sys.stdin, 'isatty', return_value=False), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(runner, 'validate_board_disk', return_value=board), \
                patch.object(runner.os, 'execvp') as execute:
            runner.main()
        command = execute.call_args.args[1]
        self.assertEqual(command[command.index('-netdev') + 1], 'user,id=physicalnet')
        self.assertIn('virtio-net-pci,addr=2.0,disable-legacy=on,romfile=,id=physicalnet,netdev=physicalnet,iommu_platform=on', command)
        self.assertIn('virtio-scsi-pci,addr=1.0,disable-legacy=on,id=physicalscsi,iommu_platform=on', command)
        self.assertIn('iommu=smmuv3', command[command.index('-machine') + 1].split(','))

    def test_mmio_compatibility_does_not_silently_select_pci(self):
        from board_config import Board
        source = json.loads((Path(__file__).resolve().parents[2] / 'boards/qemu.json').read_text())
        source['io-vm']['io-device'] = {'profile': 'virtio-mmio-scsi', 'compatible': 'virtio,mmio'}
        source['io-vm']['network-device'] = {'profile': 'virtio-mmio-net', 'compatible': 'virtio,mmio'}
        board = Board.parse(source)
        self.assertEqual(runner.machine_arguments(board, 'virt'), 'virt')
        self.assertTrue(runner.storage_controller(board).startswith('virtio-scsi-device,'))
        self.assertTrue(runner.network_arguments(board)[-1].startswith('virtio-net-device,'))

    def test_pci_cannot_bypass_the_host_iommu_through_an_environment_override(self):
        from board_config import Board
        board = Board.load(Path(__file__).resolve().parents[2] / 'boards/qemu.json')
        for machine in ('virt,iommu=none', 'virt,default-bus-bypass-iommu=on'):
            with self.assertRaises(ValueError):
                runner.machine_arguments(board, machine)

    def test_monitor_is_only_enabled_for_terminal_input(self):
        for interactive in (False, True):
            with self.subTest(interactive=interactive), \
                    patch.object(sys, 'argv', ['run-io-vm.py', '--qemu', 'qemu',
                                 '--image', 'kernel.img', '--initramfs', 'init.cpio',
                                 '--disk', 'disk.img']), \
                    patch.object(sys.stdin, 'isatty', return_value=interactive), \
                    patch.object(Path, 'is_file', return_value=True), \
                    patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=8 * 1024 * 1024)), \
                    patch.object(runner.os, 'execvp') as execute:
                runner.main()
                command = execute.call_args.args[1]
                self.assertEqual(command[command.index('-serial') + 1],
                                 'mon:stdio' if interactive else 'stdio')
                if interactive:
                    self.assertNotIn('-monitor', command)
                else:
                    self.assertEqual(command[command.index('-monitor') + 1], 'none')

    def test_board_boot_rejects_blank_and_mismatched_disk(self):
        from board_config import Board, SECTOR
        pack_spec = importlib.util.spec_from_file_location(
            'board_packer_test', Path(__file__).resolve().parents[2] / 'scripts/pack-board-image.py')
        packer = importlib.util.module_from_spec(pack_spec)
        pack_spec.loader.exec_module(packer)
        config = Path(__file__).resolve().parents[2] / 'boards/qemu.json'
        board = Board.load(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disk.img'
            with path.open('wb') as stream:
                stream.truncate(board.disk_sectors * SECTOR)
            with self.assertRaises(ValueError):
                runner.validate_board_disk(path, config)
            with path.open('r+b') as stream:
                for sector, data in packer.gpt(board):
                    stream.seek(sector * SECTOR)
                    stream.write(data)
            runner.validate_board_disk(path, config)
            alternate = Path(directory) / 'board.json'
            policy = json.loads(config.read_bytes())
            policy['disk']['uuid'] = 'd440bc51-21eb-4c60-96a3-eb350086328d'
            alternate.write_text(json.dumps(policy))
            with self.assertRaises(ValueError):
                runner.validate_board_disk(path, alternate)

    def test_create_and_reuse_does_not_truncate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disk.img'
            runner.prepare_disk(path, 8 * 1024 * 1024)
            with path.open('r+b') as stream:
                stream.write(b'existing volume')
            runner.prepare_disk(path, 64 * 1024 * 1024)
            self.assertEqual(path.stat().st_size, 8 * 1024 * 1024)
            with path.open('rb') as stream:
                self.assertEqual(stream.read(15), b'existing volume')

    def test_invalid_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disk.img'
            path.write_bytes(b'not a disk')
            with self.assertRaises(ValueError):
                runner.prepare_disk(path, 8 * 1024 * 1024)
            self.assertEqual(path.read_bytes(), b'not a disk')


if __name__ == '__main__':
    unittest.main()
