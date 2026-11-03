#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""System-volume refresh must preserve persistent data and fail atomically."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from board_config import Board, SECTOR

spec = importlib.util.spec_from_file_location('pack_board', ROOT / 'scripts/pack-board-image.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class SystemRefreshTests(unittest.TestCase):
    def setUp(self):
        self.prepare_board('qemu.json')

    def prepare_board(self, name):
        temporary = tempfile.TemporaryDirectory(prefix='hyper system update ')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        source = json.loads((ROOT / 'boards' / name).read_text())
        for part, size in zip(source['disk']['partitions'], (64, 8, 8)):
            part['size-mib'] = size
        self.board = Board.parse(source)
        self.system = next(part for part in self.board.partitions if part.content == 'system')
        self.start = self.system.start * SECTOR
        self.end = (self.system.end + 1) * SECTOR
        self.output = self.directory / 'disk.img'
        with self.output.open('xb') as stream:
            stream.truncate(self.board.disk_sectors * SECTOR)
            for sector, data in packer.gpt(self.board):
                stream.seek(sector * SECTOR)
                stream.write(data)
            for part in self.board.partitions:
                for sector in (part.start, (part.start + part.end) // 2, part.end):
                    stream.seek(sector * SECTOR)
                    stream.write((part.name + ': persistent user bytes').encode())
            stream.seek(self.start)
            stream.write(b'X' * (self.end - self.start))
        self.root = self.directory / 'system-root'
        self.root.mkdir()
        (self.root / 'tool').write_bytes(b'new program')
        self.state = packer.system_state_path(self.output)
        self.state.write_text('{"files": "old system"}\n')
        self.original_hash = packer.digest(self.output)
        self.original_state = self.state.read_bytes()

    def packed(self, root, image, tool):
        image.write_bytes(b'\0' * 4096 + (root / 'tool').read_bytes())
        return image

    def unchanged(self):
        self.assertEqual(packer.digest(self.output), self.original_hash)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertFalse(list(self.directory.glob('.board-update-*')))

    def test_make_refresh_preserves_gpt_data_guest_and_clears_old_system_tail(self):
        before = self.output.read_bytes()
        config = self.directory / 'deployment.json'
        config.write_text(json.dumps(self.board.source))
        # A deterministic formatter stub exercises the real CLI and Make recipe.
        tool = self.directory / 'mkfs.erofs'
        tool.write_text('#!/bin/sh\nprintf "new filesystem" > "$6"\n')
        tool.chmod(0o755)
        for target in ('board-build', 'rebuild', 'board-rebuild'):
            with self.subTest(target=target):
                # Exercise actual refresh on each entry, not only its no-op path.
                self.state.write_bytes(self.original_state)
                subprocess.run(['make', '-o', 'image', '-o', 'board-initramfs',
                                '-o', 'board-guest-images', target,
                                f'BOARD_OUTPUT={self.directory}', f'EROFS_MKFS={tool}'],
                               cwd=ROOT, check=True, capture_output=True, text=True)
                current = self.output.read_bytes()
                self.assertEqual(current[:self.start], before[:self.start])
                self.assertEqual(current[self.end:], before[self.end:])
        after = self.output.read_bytes()
        self.assertEqual(after[:self.start], before[:self.start])
        self.assertEqual(after[self.end:], before[self.end:])
        self.assertEqual(after[self.start:self.end],
                         b'new filesystem' + b'\0' * (self.end - self.start - 14))
        packer.check_system_payload(self.output, self.root, self.board)
        saved = packer.disk_stamp(self.output.stat())
        with patch.object(packer, 'prepare_erofs', side_effect=AssertionError('no rebuild')):
            self.assertFalse(packer.refresh_system_payload(self.board, self.output, self.root))
        self.assertEqual(packer.disk_stamp(self.output.stat()), saved)

    def test_rpi5_rebuild_preserves_data_and_firmware_partition(self):
        self.prepare_board('rpi5-native.json')
        self.test_make_refresh_preserves_gpt_data_guest_and_clears_old_system_tail()

    def test_missing_sidecar_can_be_repaired_without_resetting_data(self):
        self.state.unlink()
        before = self.output.read_bytes()
        with patch.object(packer, 'prepare_erofs', side_effect=self.packed):
            self.assertTrue(packer.refresh_system_payload(self.board, self.output, self.root))
        after = self.output.read_bytes()
        self.assertEqual(after[:self.start], before[:self.start])
        self.assertEqual(after[self.end:], before[self.end:])
        self.assertEqual(after[self.start:self.start + 4096], b'\0' * 4096)
        self.assertEqual(after[self.start + 4096:self.start + 4107], b'new program')
        packer.check_system_payload(self.output, self.root, self.board)

    def test_explicit_reset_replaces_persistent_bytes_only_after_packing(self):
        (self.directory / 'deployment.json').write_text(json.dumps(self.board.source))
        (self.directory / 'alpine.itb').write_bytes(b'guest image fixture')
        guest = next(part for part in self.board.partitions if part.image == 'alpine-rootfs')
        with (self.directory / 'alpine.ext4').open('wb') as stream:
            stream.write(b'guest disk fixture')
            stream.truncate(guest.sectors * SECTOR)
        # Only external formatters are substituted; Make, GPT construction,
        # payload assembly and atomic disk publication use production code.
        tools = self.directory / 'tools'
        tools.mkdir()
        for name, body in [('mkfs.fat', 'exit 0'), ('mcopy', 'exit 0'),
                           ('mkfs.erofs', 'printf "new filesystem" > "$6"')]:
            tool = tools / name
            tool.write_text('#!/bin/sh\n' + body + '\n')
            tool.chmod(0o755)
        command = ['make', '-o', 'image', '-o', 'board-initramfs',
                   '-o', 'board-guest-images', 'board-reset-storage',
                   f'BOARD_OUTPUT={self.directory}', f'EROFS_MKFS={tools / "mkfs.erofs"}']
        environment = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']}
        formatter = tools / 'mkfs.erofs'
        formatter.write_text('#!/bin/sh\nexit 1\n')
        failed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.unchanged()
        formatter.write_text('#!/bin/sh\nprintf "new filesystem" > "$6"\n')
        reset = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
        self.assertEqual(reset.returncode, 0, reset.stdout + reset.stderr)
        with self.output.open('rb') as stream:
            packer.validate_disk(stream, self.board)
            for part in self.board.partitions:
                stream.seek(part.start * SECTOR)
                self.assertNotIn(b'persistent user bytes', stream.read(SECTOR))
        self.assertNotEqual(packer.digest(self.output), self.original_hash)
        packer.check_system_payload(self.output, self.root, self.board)

    def test_nonboard_rebuild_preserves_an_existing_io_disk(self):
        for arch, profile in [('aarch64', 'io'), ('aarch64', 'native'),
                              ('riscv64', 'native'), ('x86_64', 'native')]:
            with self.subTest(arch=arch, profile=profile):
                subprocess.run(['make', '-o', 'image', '-o', 'native-initramfs',
                                '-o', 'io-initramfs', 'rebuild', f'ARCH={arch}',
                                f'RUN_PROFILE={profile}', f'IO_VM_DISK={self.output}'],
                               cwd=ROOT, check=True, capture_output=True, text=True)
                self.unchanged()

    def test_packing_and_publication_failures_keep_original_disk(self):
        with patch.object(packer, 'prepare_erofs', side_effect=ValueError('mkfs failed')):
            with self.assertRaisesRegex(ValueError, 'mkfs failed'):
                packer.refresh_system_payload(self.board, self.output, self.root)
        self.unchanged()
        with patch.object(packer, 'prepare_erofs', side_effect=self.packed), \
                patch.object(packer.os, 'replace', side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(OSError, 'publication failed'):
                packer.refresh_system_payload(self.board, self.output, self.root)
        self.unchanged()

    def test_oversize_or_changing_system_does_not_publish(self):
        def oversized(*args):
            image = self.packed(*args)
            with image.open('r+b') as stream:
                stream.truncate(self.end - self.start + 1)
            return image

        def changed(*args):
            image = self.packed(*args)
            (self.root / 'tool').write_bytes(b'changed during packing')
            return image

        for build, error in ((oversized, 'exceeds its partition'), (changed, 'payload changed')):
            with patch.object(packer, 'prepare_erofs', side_effect=build):
                with self.assertRaisesRegex(ValueError, error):
                    packer.refresh_system_payload(self.board, self.output, self.root)
            self.unchanged()

    def test_mismatched_gpt_is_rejected_before_formatting(self):
        with self.output.open('r+b') as stream:
            stream.seek((self.board.disk_sectors - 1) * SECTOR)
            stream.write(b'bad backup GPT')
        self.original_hash = packer.digest(self.output)
        with patch.object(packer, 'prepare_erofs') as build:
            with self.assertRaisesRegex(ValueError, 'existing disk GPT'):
                packer.refresh_system_payload(self.board, self.output, self.root)
            build.assert_not_called()
        self.unchanged()

    def test_busy_disk_is_rejected_before_formatting(self):
        # A separate process is necessary: POSIX record locks are per-process.
        locker = subprocess.Popen([sys.executable, '-c',
                                   'import fcntl, sys; f = open(sys.argv[1], "r+b"); '
                                   'fcntl.lockf(f, fcntl.LOCK_SH, 1, 100); '
                                   'print("locked", flush=True); sys.stdin.read()', str(self.output)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(locker.stdout.readline(), 'locked\n')
            with patch.object(packer, 'prepare_erofs') as build:
                with self.assertRaisesRegex(ValueError, 'in use'):
                    packer.refresh_system_payload(self.board, self.output, self.root)
                build.assert_not_called()
        finally:
            locker.communicate(timeout=10)
        self.unchanged()

    def test_concurrent_disk_writer_is_detected_without_publishing(self):
        def changed(*args):
            image = self.packed(*args)
            with self.output.open('r+b') as stream:
                stream.seek(self.board.partitions[0].start * SECTOR)
                stream.write(b'concurrent data write')
            self.original_hash = packer.digest(self.output)
            return image

        with patch.object(packer, 'prepare_erofs', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'disk image changed'):
                packer.refresh_system_payload(self.board, self.output, self.root)
        self.unchanged()

    def test_writable_system_volume_requires_explicit_replacement(self):
        policy = self.board.source
        policy['disk']['partitions'][1]['filesystem'] = 'fat'
        board = Board.parse(policy)
        with self.output.open('r+b') as stream:
            for sector, data in packer.gpt(board):
                stream.seek(sector * SECTOR)
                stream.write(data)
        self.original_hash = packer.digest(self.output)
        with self.assertRaisesRegex(ValueError, 'generated EROFS'):
            packer.refresh_system_payload(board, self.output, self.root)
        self.unchanged()

    def test_real_erofs_refresh(self):
        mkfs = ROOT / 'target/host-tools/erofs/bin/mkfs.erofs'
        if not mkfs.is_file():
            try:
                mkfs = packer.image_tool('mkfs.erofs', 'mkfs.erofs', 'erofs-utils')
            except ValueError as error:
                self.skipTest(str(error))
        before = self.output.read_bytes()
        self.assertTrue(packer.refresh_system_payload(self.board, self.output, self.root, str(mkfs)))
        after = self.output.read_bytes()
        self.assertEqual(after[:self.start], before[:self.start])
        self.assertEqual(after[self.end:], before[self.end:])
        self.assertEqual(after[self.start + 1024:self.start + 1028], b'\xe2\xe1\xf5\xe0')
        packer.check_system_payload(self.output, self.root, self.board)


if __name__ == '__main__':
    unittest.main()
