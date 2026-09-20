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
        files = {'bcm2712-rpi-5-b.dtb': b'\xd0\x0d\xfe\xedhost',
                 'bcm2712d0-rpi-5-b.dtb': b'\xd0\x0d\xfe\xedhost-d0',
                 'overlays/bcm2712d0.dtbo': b'\xd0\x0d\xfe\xedoverlay',
                 'overlays/overlay_map.dtb': b'\xd0\x0d\xfe\xedmap',
                 'boot-notices.txt': b'upstream notices', 'sources.tar.gz': b'sources'}
        for name, contents in files.items():
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_bytes(contents)
        manifest = {'format': 'hyper.rpi5-boot.v2', 'sha256': {
            name: hashlib.sha256(contents).hexdigest() for name, contents in files.items()}}
        (directory / 'manifest.json').write_text(json.dumps(manifest))

    def test_local_qemu_artifact_cannot_claim_pi_platform(self):
        spec = importlib.util.spec_from_file_location('io_images', ROOT / 'scripts/io-vm-images.py')
        images = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(images)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for platform in (None, 'qemu'):
                metadata = {'format': 1, 'architecture': 'aarch64'}
                if platform is not None:
                    metadata['platform'] = platform
                (output / 'boot-artifacts.json').write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, 'platform mismatch'):
                    images.package_payloads(output, 'rpi5')

    def test_diskless_io_config_has_supervision_without_device_assignment(self):
        spec = importlib.util.spec_from_file_location('io_images', ROOT / 'scripts/io-vm-images.py')
        images = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(images)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            images.bringup_config(output)
            manifest = json.loads((output / 'services.json').read_text())
            names = {service['name'] for service in manifest['services']}
            self.assertIn('vm-manager', names)
            self.assertIn('session', names)
            self.assertNotIn('io-runtime', names)
            self.assertEqual(manifest['virtual-machines']['config'], '/etc/hyper/vms.json')
            for service in manifest['services']:
                for cap in service['capabilities']:
                    self.assertNotEqual(cap['source'], 'bootstrap.device-assignment-authority')
            vms = json.loads((output / 'vms.json').read_text())
            self.assertEqual(vms['virtual-machines'], [
                {'name': 'io-bringup', 'image': '/vm/io.itb', 'autostart': False}])

    def test_sd_profile_exports_configuration_and_qemu_equivalent_alpine(self):
        from board_bootstrap import services
        board = bringup.Board.load(ROOT / 'boards/rpi5-sd.json')
        self.assertEqual(board.source['io-device'], {
            'profile': 'bcm2712-sdhci', 'path': '/soc@107c000000/mmc@fff000'})
        self.assertEqual(board.source['disk']['config-mib'], 1024)
        self.assertEqual(len(board.partitions), 2)
        qemu = bringup.Board.load(ROOT / 'boards/qemu.json')
        self.assertEqual(board.source['virtual-machines'], qemu.source['virtual-machines'])
        self.assertEqual(board.source['files']['vm/alpine.itb'], 'alpine')
        manifest = services(board, ROOT)
        self.assertEqual(manifest['virtual-machines']['config'], '/data/vms.json')
        runtime = next(item for item in manifest['services'] if item['name'] == 'io-runtime')
        self.assertIn('io.ready', [cap['purpose'] for cap in runtime['capabilities']])
        self.assertIn('hyper-config', board.bootstrap_volumes())
        self.assertIn('alpine', board.bootstrap_volumes())

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
                if name == 'hyper':
                    header = bytearray(64)
                    header[16:24] = (4096).to_bytes(8, 'little')
                    header[56:60] = b'ARM\x64'
                    path.write_bytes(header)
                else:
                    path.write_bytes(b'payload')
                artifacts[name] = path
            payload = directory / 'payload'
            payload.mkdir()
            board = bringup.Board.load(ROOT / 'boards/rpi5-native.json')
            bringup.packer.prepare_payload(board, artifacts, payload)
            self.assertEqual(artifacts['hyper'].stat().st_size, 64)
            self.assertEqual((payload / 'hyper.img').stat().st_size, 4096)
            for name in ('bcm2712-rpi-5-b.dtb', 'bcm2712d0-rpi-5-b.dtb',
                         'overlays/bcm2712d0.dtbo', 'overlays/overlay_map.dtb'):
                self.assertEqual((payload / name).read_bytes(), (directory / name).read_bytes())
            config = (payload / 'config.txt').read_text()
            self.assertNotIn('device_tree=', config)
            self.assertNotIn('armstub=', config)
            self.assertFalse((payload / 'bl31.bin').exists())
            self.assertIn('kernel=hyper.img\n', config)
            self.assertIn('initramfs bootstrap.cpio followkernel\n', config)
            self.assertEqual((payload / 'boot-notices.txt').read_bytes(), b'upstream notices')

    def test_modified_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.package(directory)
            (directory / 'bcm2712-rpi-5-b.dtb').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                bringup.boot_artifacts(directory)

    def test_missing_d0_tree_is_rejected_even_with_matching_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.package(directory)
            name = 'bcm2712d0-rpi-5-b.dtb'
            (directory / name).unlink()
            manifest = json.loads((directory / 'manifest.json').read_text())
            del manifest['sha256'][name]
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'boot package must include'):
                bringup.boot_artifacts(directory)

    def test_missing_stepping_overlay_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.package(directory)
            (directory / 'overlays/bcm2712d0.dtbo').unlink()
            with self.assertRaisesRegex(ValueError, 'missing or empty'):
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
