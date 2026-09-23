#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise host admission of shipped/generated manifests and packaging failures."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from board_config import Board
from board_bootstrap import services

CHECK = ROOT / 'scripts/check-service-manifest.py'
BASE = ROOT / 'app/init/config/native/services.json'


class ManifestTests(unittest.TestCase):
    def check_manifest(self, path, images=()):
        return subprocess.run([sys.executable, str(CHECK), str(path), *images],
                              capture_output=True, text=True)

    def test_shipped_and_generated(self):
        for path in (ROOT / 'app/init').rglob('services.json'):
            with self.subTest(path=path):
                result = self.check_manifest(path)
                self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            for board in ('qemu', 'rpi5'):
                source = json.loads((ROOT / f'boards/{board}.json').read_text())
                path.write_text(json.dumps(services(Board.parse(source), ROOT)))
                result = self.check_manifest(path)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_invalid_policy_and_syntax(self):
        original = json.loads(BASE.read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            cases = ['{', BASE.read_text().replace('"format":', '"format": "duplicate", "format":', 1)]
            for mutation in ('dependency', 'cycle', 'rights', 'restart', 'unknown'):
                value = copy.deepcopy(original)
                service = value['services'][0]
                if mutation == 'dependency':
                    service['after'] = ['missing-service']
                elif mutation == 'cycle':
                    service['after'] = [service['name']]
                elif mutation == 'rights':
                    service['capabilities'][0]['rights'] = ['invented-right']
                elif mutation == 'restart':
                    service['restart'] = 'always'
                else:
                    service['typo'] = True
                cases.append(json.dumps(value))
            for text in cases:
                path.write_text(text)
                result = self.check_manifest(path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(path), result.stderr)
            result = self.check_manifest(BASE, ['bin/nonexistent'])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('absent from the archive', result.stderr)

    def test_packer_rejects_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'services.json'
            path.write_text('{')
            output = Path(temporary) / 'initramfs.cpio'
            output.write_bytes(b'previous valid image')
            result = subprocess.run([
                sys.executable, str(ROOT / 'scripts/pack-native-initramfs.py'),
                '--packer', 'does-not-exist', '--strip', 'does-not-exist',
                '--output', str(output), '0644', 'etc/hyper/services.json', str(path),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('service manifest:', result.stderr)
            self.assertEqual(output.read_bytes(), b'previous valid image')


if __name__ == '__main__':
    unittest.main()
