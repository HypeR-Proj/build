#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Protect persistent disks used by interactive I/O VM boots."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

spec = importlib.util.spec_from_file_location(
    'io_runner', Path(__file__).resolve().parents[2] / 'scripts/run-io-vm.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class DiskTests(unittest.TestCase):
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
