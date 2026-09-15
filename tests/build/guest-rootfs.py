#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Check guest ext4 packaging preserves Linux ownership and failed-build output."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('guest_disk', ROOT / 'scripts/pack-guest-disk.py')
guest_disk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest_disk)


class GuestDiskTests(unittest.TestCase):
    def test_rootfs_ownership_and_failed_rebuild(self):
        try:
            debugfs = guest_disk.packer.image_tool('debugfs', 'debugfs', 'e2fsprogs')
            guest_disk.packer.image_tool('mke2fs', 'mke2fs', 'e2fsprogs')
        except ValueError as error:
            self.skipTest(str(error))
        board = json.loads((ROOT / 'boards/qemu.json').read_text())
        board['virtual-machines'][0]['disk-mib'] = 32
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            archive = temporary / 'rootfs.tar'
            with tarfile.open(archive, 'w') as target:
                info = tarfile.TarInfo('owned')
                info.size = 5
                info.uid, info.gid, info.mode = 123, 456, 0o640
                target.addfile(info, io.BytesIO(b'proof'))
            output = temporary / 'root.ext4'
            guest_disk.build(guest_disk.Board.parse(board), archive, output)
            self.assertEqual(output.stat().st_size, 32 * 1024**2)
            result = subprocess.run([debugfs, '-R', 'stat /owned', str(output)],
                                    check=True, capture_output=True, text=True)
            self.assertRegex(result.stdout, r'User:\s+123\s+Group:\s+456')
            self.assertIn('0640', result.stdout)
            restored = temporary / 'restored'
            subprocess.run([debugfs, '-R', f'dump /owned {restored}', str(output)],
                           check=True, capture_output=True)
            self.assertEqual(restored.read_bytes(), b'proof')
            before = output.stat().st_ino
            archive.write_bytes(b'not a tar')
            with self.assertRaises(tarfile.ReadError):
                guest_disk.build(guest_disk.Board.parse(board), archive, output)
            self.assertEqual(output.stat().st_ino, before)


if __name__ == '__main__':
    unittest.main()
