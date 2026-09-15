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
        actual = packer.artifact_inputs(board, defaults, ['custom=my-guest', 'hyper=my-kernel'])
        self.assertNotIn('alpine', actual)
        self.assertEqual(actual['custom'], Path('my-guest'))
        self.assertEqual(actual['hyper'], Path('my-kernel'))
        for explicit in ([], ['custom=x', 'typo=y'], ['custom=x', 'custom=y']):
            with self.assertRaises(ValueError):
                packer.artifact_inputs(board, defaults, explicit)

    def test_board_profiles_have_same_abi_and_explicit_volumes(self):
        for profile in ('qemu', 'rpi5'):
            board = Board.load(ROOT / f'boards/{profile}.json')
            self.assertEqual(board.source['architecture'], 'aarch64')
            self.assertEqual(board.partitions[0].sectors * SECTOR, 1024**3)
            self.assertEqual(board.partitions[0].owner, 'hyper')
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
                for part in board.partitions:
                    if part.image:
                        artifacts[part.image] = root / part.image
                        with artifacts[part.image].open('wb') as disk:
                            disk.truncate(part.sectors * SECTOR)
                payload = root / 'payload'
                payload.mkdir()
                packer.prepare_payload(board, artifacts, payload)
                self.assertFalse((payload / 'vm/io.itb').exists())
                self.assertEqual((payload / 'bootstrap.cpio').is_file(), profile == 'rpi5')
                self.assertTrue((payload / 'vm/alpine.itb').is_file())

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
            source['io-device'] = selector
            with self.assertRaises(ValueError):
                Board.parse(source)
        source = copy.deepcopy(self.source)
        source['io-device'] = {'profile': 'virtio-mmio-scsi', 'path': '/soc/virtio@a000000'}
        Board.parse(source)
        source['virtual-machines'] = [dict(source['virtual-machines'][0], name=f'vm{i}') for i in range(9)]
        with self.assertRaises(ValueError):
            Board.parse(source)

    def test_bootstrap_projection_uses_same_volume_identity(self):
        board = Board.parse(self.source)
        lines = board.bootstrap_volumes().splitlines()
        self.assertEqual(lines[0], 'hyper.volumes.v1')
        for line, volume in zip(lines[1:], board.volumes()['volumes'], strict=True):
            self.assertEqual(line.split(), [volume['name'], volume['partuuid'],
                             str(volume['sectors']), volume['owner'], volume['mapper']])
        self.assertEqual(board.bootstrap_clients(), 'hyper.clients.v1\n0 config\n1 alpine\n')
        self.assertEqual(board.vms()['virtual-machines'][0]['disk'],
                         {'client': 1, 'volume': 'alpine'})

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
            case['disk']['config-mib'] = size
            cases.append(case)
        for path in ('../escape', '/absolute', 'vm/../bad', 'VM/ALPINE.ITB',
                     'vm', 'volumes.json', 'config.txt', 'x\\y', 'x/./y'):
            case = copy.deepcopy(self.source)
            case['files'][path] = 'artifact'
            cases.append(case)
        case = copy.deepcopy(self.source)
        case['virtual-machines'] *= 2
        cases.append(case)
        case = copy.deepcopy(self.source)
        case['virtual-machines'][0]['name'] = 'config'
        cases.append(case)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                Board.parse(case)

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
        self.source['disk']['config-mib'] = 64
        self.source['virtual-machines'][0]['disk-mib'] = 8
        self.source['virtual-machines'][0]['disk-image'] = 'guest-disk'
        board = Board.parse(self.source)
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
            config_file = directory / 'volumes.json'
            subprocess.run([mcopy, '-i', f'{output}@@{board.partitions[0].start * SECTOR}',
                            '::/volumes.json', str(config_file)], check=True)
            self.assertEqual(json.loads(config_file.read_bytes()), board.volumes())
            with output.open('rb') as stream:
                stream.seek(board.partitions[1].start * SECTOR)
                self.assertEqual(stream.read(17), b'guest-sector-zero')
            self.assertEqual(disk.stat().st_size, 8 * 1024**2)


if __name__ == '__main__':
    unittest.main()
