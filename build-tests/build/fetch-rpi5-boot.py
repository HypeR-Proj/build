#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Verify Pi boot download integrity and atomic cache publication."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fetch_boot', ROOT / 'scripts/fetch-rpi5-boot.py')
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'cache'
        lock = json.loads(fetcher.LOCK.read_text())
        self.payloads = {}
        for name, item in lock['files'].items():
            data = b'\xd0\x0d\xfe\xed' + name.encode()
            self.payloads[item['url']] = data
            item['sha256'] = hashlib.sha256(data).hexdigest()
        self.lock = self.root / 'lock.json'
        self.lock.write_text(json.dumps(lock))
        override = patch.object(fetcher, 'LOCK', self.lock)
        override.start()
        self.addCleanup(override.stop)

    def download(self, url, **kwargs):
        return io.BytesIO(self.payloads[url])

    def test_publish_and_reuse_without_network(self):
        with patch.object(fetcher.urllib.request, 'urlopen', side_effect=self.download):
            result = fetcher.fetch(self.output)
        with patch.object(fetcher.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            self.assertEqual(result, fetcher.fetch(self.output))
        manifest = json.loads((result / 'manifest.json').read_text())
        self.assertEqual(manifest['format'], 'hyper.rpi5-boot.v2')
        self.assertEqual(len(manifest['sha256']), 6)
        self.assertEqual((result / 'rpi5-boot.lock.json').read_bytes(), self.lock.read_bytes())

    def test_checksum_failure_does_not_publish(self):
        with patch.object(fetcher.urllib.request, 'urlopen', return_value=io.BytesIO(b'bad')):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                fetcher.fetch(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_interrupted_download_does_not_publish(self):
        with patch.object(fetcher.urllib.request, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaisesRegex(OSError, 'offline'):
                fetcher.fetch(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cache_tampering_is_rejected(self):
        with patch.object(fetcher.urllib.request, 'urlopen', side_effect=self.download):
            result = fetcher.fetch(self.output)
        (result / 'bcm2712-rpi-5-b.dtb').write_bytes(b'corrupt')
        with patch.object(fetcher.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                fetcher.fetch(self.output)


if __name__ == '__main__':
    unittest.main()
