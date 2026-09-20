#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Pi 5 Native image composition and external boot input integrity."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('rpi5_bringup', ROOT / 'scripts/rpi5-bringup.py')
bringup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bringup)


class BringupTests(unittest.TestCase):
    def package(self, directory):
        files = {'bl31.bin': b'firmware', 'bcm2712-rpi-5-b.dtb': b'\xd0\x0d\xfe\xedhost',
                 'boot-notices.txt': b'upstream notices', 'sources.tar.gz': b'sources'}
        for name, contents in files.items():
            (directory / name).write_bytes(contents)
        manifest = {'format': 'hyper.rpi5-boot.v1', 'sha256': {
            name: hashlib.sha256(contents).hexdigest() for name, contents in files.items()}}
        (directory / 'manifest.json').write_text(json.dumps(manifest))

    def test_native_profile_has_no_guest_disk_or_io_payload(self):
        board = bringup.Board.load(ROOT / 'boards/rpi5-native.json')
        self.assertEqual(len(board.partitions), 1)
        self.assertEqual(board.source['virtual-machines'], [])
        self.assertEqual(board.source['disk']['config-mib'], 64)
        self.assertNotIn('io-vm', board.source['files'].values())
        services = json.loads((ROOT / 'app/init/config/native/services.json').read_text())
        self.assertNotIn('io-runtime', [item['name'] for item in services['services']])

    def test_payload_has_firmware_boot_config_and_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.package(directory)
            artifacts = bringup.boot_artifacts(directory)
            for name in ['hyper', 'bootstrap']:
                path = directory / name
                path.write_bytes(b'payload')
                artifacts[name] = path
            payload = directory / 'payload'
            payload.mkdir()
            board = bringup.Board.load(ROOT / 'boards/rpi5-native.json')
            bringup.packer.prepare_payload(board, artifacts, payload)
            config = (payload / 'config.txt').read_text()
            self.assertIn('armstub=bl31.bin\n', config)
            self.assertIn('initramfs bootstrap.cpio followkernel\n', config)
            self.assertEqual((payload / 'boot-notices.txt').read_bytes(), b'upstream notices')

    def test_modified_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.package(directory)
            (directory / 'bl31.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                bringup.boot_artifacts(directory)

    def test_missing_corresponding_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.package(directory)
            (directory / 'sources.tar.gz').unlink()
            with self.assertRaisesRegex(ValueError, 'missing or empty'):
                bringup.boot_artifacts(directory)


if __name__ == '__main__':
    unittest.main()
