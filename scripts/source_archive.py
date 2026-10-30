#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Materialize explicitly fetched, SHA-256 pinned source archives."""

import errno
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile


def sources(manifest, cache, fetch=False):
    """Return immutable source roots; ordinary builds never access the network."""
    result = {}
    for record in json.loads(Path(manifest).read_text())['archives']:
        digest = record['sha256']
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid source digest')
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
                if hashlib.sha256(downloaded.read_bytes()).hexdigest() != digest:
                    raise ValueError(f'source digest mismatch: {record["url"]}')
                downloaded.replace(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise ValueError(f'source digest mismatch: {archive}')
        destination = cache / 'unpacked' / digest
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
                tree = Path(temporary) / 'source'
                tree.mkdir()
                with tarfile.open(archive) as bundle:
                    for member in bundle:
                        path = PurePosixPath(member.name)
                        if path.is_absolute() or '..' in path.parts:
                            raise ValueError('archive path escapes source root')
                        # The dependency closure needs no links, device nodes or ownership data.
                        if not member.isfile():
                            continue
                        parts = path.parts[record.get('strip_components', 0):]
                        if not parts:
                            raise ValueError('empty archive file path')
                        target = tree.joinpath(*parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.extractfile(member) as source, target.open('wb') as output:
                            shutil.copyfileobj(source, output)
                try:
                    tree.rename(destination)
                except OSError as error:
                    # Another build can publish the same verified archive first.
                    if error.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not destination.is_dir():
                        raise
        result[record['path']] = destination
    return result
