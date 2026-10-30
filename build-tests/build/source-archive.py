#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Pin validation and extraction safety for experimental source dependencies."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_archive import sources


class Archives(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / 'sources.json'
        self.cache = self.root / 'cache'

    def archive(self, name='prefix/file'):
        output = self.root / 'input.tar.gz'
        with tarfile.open(output, 'w:gz') as bundle:
            entry = tarfile.TarInfo(name)
            entry.size = 7
            bundle.addfile(entry, io.BytesIO(b'payload'))
        data = output.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        path = self.cache / 'archives' / (digest + '.tar.gz')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.manifest.write_text(json.dumps({'archives': [{
            'path': 'test', 'sha256': digest, 'strip_components': 1,
            'url': 'https://invalid.example/not-accessed',
        }]}))
        return path

    def test_offline_and_digest_even_after_materialization(self):
        path = self.archive()
        roots = sources(self.manifest, self.cache)
        self.assertEqual((roots['test'] / 'file').read_bytes(), b'payload')
        self.assertEqual(roots, sources(self.manifest, self.cache))
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            sources(self.manifest, self.cache)

    def test_missing_archive_never_implicitly_downloads(self):
        self.archive().unlink()
        with self.assertRaises(FileNotFoundError):
            sources(self.manifest, self.cache)

    def test_traversal_rejected_before_stripping(self):
        self.archive('../escaped')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            sources(self.manifest, self.cache)
        self.assertFalse((self.root / 'escaped').exists())


if __name__ == '__main__':
    unittest.main()
