#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Pin validation and extraction safety for experimental source dependencies."""
import hashlib
import concurrent.futures
import errno
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_archive import archive_digest, sources


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

    def test_modified_missing_and_extra_extracted_content_is_rejected(self):
        self.archive()
        directory = sources(self.manifest, self.cache)['test']
        file = directory / 'file'
        file.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'extracted source'):
            sources(self.manifest, self.cache)
        file.unlink()
        with self.assertRaisesRegex(ValueError, 'extracted source'):
            sources(self.manifest, self.cache)
        file.write_bytes(b'payload')
        extra = directory / 'extra'
        extra.write_bytes(b'untracked')
        with self.assertRaisesRegex(ValueError, 'extracted source'):
            sources(self.manifest, self.cache)
        extra.unlink()
        self.assertEqual(sources(self.manifest, self.cache)['test'], directory)

    def test_extraction_policy_has_a_distinct_cache_identity(self):
        self.archive()
        stripped = sources(self.manifest, self.cache)['test']
        manifest = json.loads(self.manifest.read_text())
        manifest['archives'][0]['strip_components'] = 0
        self.manifest.write_text(json.dumps(manifest))
        complete = sources(self.manifest, self.cache)['test']
        self.assertNotEqual(stripped, complete)
        self.assertEqual((complete / 'prefix/file').read_bytes(), b'payload')
        self.assertEqual((stripped / 'file').read_bytes(), b'payload')

    def test_extracted_links_and_extra_directories_are_rejected(self):
        self.archive()
        directory = sources(self.manifest, self.cache)['test']
        file = directory / 'file'
        file.unlink()
        outside = self.root / 'outside'
        outside.write_bytes(b'payload')
        file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'extracted source'):
            sources(self.manifest, self.cache)
        file.unlink()
        file.write_bytes(b'payload')
        (directory / 'extra').mkdir()
        with self.assertRaisesRegex(ValueError, 'extracted source'):
            sources(self.manifest, self.cache)
        (directory / 'extra').rmdir()
        moved = directory.with_name('moved')
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'extracted source root'):
            sources(self.manifest, self.cache)

    def test_invalid_strip_policy_is_rejected_before_materialization(self):
        self.archive()
        data = json.loads(self.manifest.read_text())
        for value in (-1, True, '1', 1.5):
            with self.subTest(value=value):
                data['archives'][0]['strip_components'] = value
                self.manifest.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'strip_components'):
                    sources(self.manifest, self.cache)
        self.assertFalse((self.cache / 'unpacked').exists())

    def test_concurrent_publishers_return_the_same_verified_generation(self):
        self.archive()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            roots = list(workers.map(lambda _: sources(self.manifest, self.cache)['test'], range(4)))
        self.assertEqual(len(set(roots)), 1)
        self.assertEqual((roots[0] / 'file').read_bytes(), b'payload')
        self.assertEqual(list((self.cache / 'unpacked').iterdir()), [roots[0]])

    def test_competing_publication_is_verified_before_acceptance(self):
        self.archive()

        def publish_corrupt_winner(source, destination):
            destination.mkdir()
            (destination / 'file').write_bytes(b'wrong')
            raise OSError(errno.EEXIST, 'another publisher won')

        with patch.object(Path, 'rename', publish_corrupt_winner):
            with self.assertRaisesRegex(ValueError, 'extracted source'):
                sources(self.manifest, self.cache)

    def test_traversal_rejected_before_stripping(self):
        self.archive('../escaped')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            sources(self.manifest, self.cache)
        self.assertFalse((self.root / 'escaped').exists())

    def test_content_pin_ignores_metadata_but_checks_names_and_bytes(self):
        archive = self.archive()
        record = json.loads(self.manifest.read_text())['archives'][0]
        del record['sha256']
        record['content_sha256'] = archive_digest(archive, content=True, strip_components=1)
        pinned = archive.with_name(record['content_sha256'] + '.tar.gz')
        self.manifest.write_text(json.dumps({'archives': [record]}))
        for name, payload, accepted in [('prefix/file', b'payload', True),
                                         ('prefix/file', b'changed', False),
                                         ('prefix/renamed', b'payload', False)]:
            with self.subTest(name=name, payload=payload):
                with tarfile.open(pinned, 'w:gz') as bundle:
                    entry = tarfile.TarInfo(name)
                    entry.size = len(payload)
                    entry.mtime = 12345
                    entry.uid = 42
                    bundle.addfile(entry, io.BytesIO(payload))
                if accepted:
                    self.assertNotEqual(archive.read_bytes(), pinned.read_bytes())
                    self.assertEqual((sources(self.manifest, self.cache)['test'] / 'file').read_bytes(), payload)
                else:
                    with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                        sources(self.manifest, self.cache)

    def test_duplicate_paths_cannot_hide_different_extraction_order(self):
        archive = self.root / 'duplicates.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for payload in (b'first', b'last'):
                entry = tarfile.TarInfo('file')
                entry.size = len(payload)
                bundle.addfile(entry, io.BytesIO(payload))
        with self.assertRaisesRegex(ValueError, 'duplicate archive file path'):
            archive_digest(archive, content=True)


if __name__ == '__main__':
    unittest.main()
