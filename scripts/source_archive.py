#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Materialize explicitly fetched, SHA-256 pinned source archives."""

import errno
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile


def archive_files(bundle, strip_components=0):
    """Yield unique regular files after validating paths before stripping."""
    if type(strip_components) is not int or strip_components < 0:
        raise ValueError('strip_components must be a nonnegative integer')
    seen = set()
    for member in bundle:
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('archive path escapes source root')
        # Links, device nodes and ownership metadata are never materialized.
        if not member.isfile():
            continue
        parts = path.parts[strip_components:]
        if not parts:
            raise ValueError('empty archive file path')
        relative = PurePosixPath(*parts)
        if relative in seen:
            raise ValueError('duplicate archive file path')
        seen.add(relative)
        yield member, relative


def archive_entries(archive, strip_components=0):
    entries = []
    with tarfile.open(archive) as bundle:
        for member, path in archive_files(bundle, strip_components):
            with bundle.extractfile(member) as source:
                entries.append([str(path), hashlib.file_digest(source, 'sha256').hexdigest()])
    return sorted(entries)


def entries_digest(entries):
    canonical = json.dumps(entries, ensure_ascii=True, separators=(',', ':')).encode()
    return hashlib.sha256(canonical).hexdigest()


def archive_digest(archive, *, content=False, strip_components=0):
    """Hash bytes, or sorted [path, file SHA-256] pairs without tar metadata."""
    if content:
        return entries_digest(archive_entries(archive, strip_components))
    with Path(archive).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def validate_extracted(directory, expected):
    """Compare the live tree with the pinned archive, never a mutable sidecar."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f'invalid extracted source root: {directory}')
    directories = set()
    entries = []
    pending = [directory]
    while pending:
        for path in pending.pop().iterdir():
            mode = path.lstat().st_mode
            relative = str(path.relative_to(directory))
            if stat.S_ISDIR(mode):
                directories.add(relative)
                pending.append(path)
            elif stat.S_ISREG(mode):
                with path.open('rb') as source:
                    entries.append([relative, hashlib.file_digest(source, 'sha256').hexdigest()])
            else:
                raise ValueError(f'unsupported extracted source entry: {path}')
    expected_directories = {str(parent) for name, _ in expected
                            for parent in PurePosixPath(name).parents if str(parent) != '.'}
    if sorted(entries) != expected or directories != expected_directories:
        raise ValueError(f'extracted source cache differs from pinned archive: {directory}; '
                         'remove this cache generation and retry')


def sources(manifest, cache, fetch=False):
    """Return immutable source roots; ordinary builds never access the network."""
    result = {}
    for record in json.loads(Path(manifest).read_text())['archives']:
        fields = [key for key in ('sha256', 'content_sha256') if key in record]
        if len(fields) != 1:
            raise ValueError('source requires exactly one digest')
        digest = record[fields[0]]
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid source digest')
        digest_options = {'content': fields[0] == 'content_sha256',
                          'strip_components': record.get('strip_components', 0)}
        strip = digest_options['strip_components']
        if type(strip) is not int or strip < 0:
            raise ValueError('strip_components must be a nonnegative integer')
        archive = cache / 'archives' / (digest + '.tar.gz')
        if not archive.exists():
            if not fetch:
                raise FileNotFoundError(f'{archive}: run the component build with --fetch first')
            archive.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
                downloaded = Path(temporary) / 'archive'
                subprocess.run(['curl', '--fail', '--location', '--proto', '=https',
                                '--proto-redir', '=https', '--retry', '2', '--max-time', '120',
                                record['url'], '-o', str(downloaded)], check=True)
                if archive_digest(downloaded, **digest_options) != digest:
                    raise ValueError(f'source digest mismatch: {record["url"]}')
                downloaded.replace(archive)
        if not digest_options['content'] and archive_digest(archive) != digest:
            raise ValueError(f'source digest mismatch: {archive}')
        expected = archive_entries(archive, strip)
        if digest_options['content'] and entries_digest(expected) != digest:
            raise ValueError(f'source digest mismatch: {archive}')
        policy = {'version': 1, 'entries': 'regular-files-only', 'digest_kind': fields[0],
                  'digest': digest, 'strip_components': strip}
        identity = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
        destination = cache / 'unpacked' / identity
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
                tree = Path(temporary) / 'source'
                tree.mkdir()
                with tarfile.open(archive) as bundle:
                    for member, path in archive_files(bundle, strip):
                        target = tree.joinpath(path)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.extractfile(member) as source, target.open('wb') as output:
                            shutil.copyfileobj(source, output)
                validate_extracted(tree, expected)
                try:
                    tree.rename(destination)
                except OSError as error:
                    # Another build can publish the same verified archive first.
                    if error.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not destination.is_dir():
                        raise
        validate_extracted(destination, expected)
        result[record['path']] = destination
    return result
