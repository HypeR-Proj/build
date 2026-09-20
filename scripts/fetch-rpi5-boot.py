#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Fetch pinned official Pi boot data and corresponding sources, without a build checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = Path(__file__).with_name('rpi5-boot.lock.json')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def notices(lock):
    return (
        'Pi 5 boot data retains upstream licenses, separately from Apache-2.0 HypeR.\n'
        f"Firmware revision: {lock['firmware']}\n"
        f"Corresponding Linux revision: {lock['linux']}\n"
        'sources.tar.gz contains the corresponding Linux source tree and license texts.\n'
        'Keep this archive, manifest.json and rpi5-boot.lock.json with redistributed images.\n'
        'EEPROM firmware and its built-in BL31 are supplied by the board vendor.\n'
    )


def fetch(output):
    raw = LOCK.read_bytes()
    lock = json.loads(raw)
    if lock['format'] != 1:
        raise ValueError('unsupported Pi boot lock')
    generation = output / hashlib.sha256(raw).hexdigest()
    output.mkdir(parents=True, exist_ok=True)

    def validate(directory):
        for name, item in lock['files'].items():
            if digest(directory / name) != item['sha256']:
                raise ValueError(f'Pi boot checksum mismatch: {name}')
        if (directory / 'boot-notices.txt').read_text() != notices(lock):
            raise ValueError('Pi boot notices mismatch')
        expected = {name: item['sha256'] for name, item in lock['files'].items()}
        expected['boot-notices.txt'] = digest(directory / 'boot-notices.txt')
        manifest = json.loads((directory / 'manifest.json').read_text())
        if manifest != {'format': 'hyper.rpi5-boot.v2', 'sha256': expected}:
            raise ValueError('Pi boot manifest mismatch')
        if (directory / 'rpi5-boot.lock.json').read_bytes() != raw:
            raise ValueError('Pi boot provenance mismatch')

    if generation.exists():
        validate(generation)
        return generation
    with tempfile.TemporaryDirectory(dir=output, prefix='.download-') as temp:
        staging = Path(temp) / 'package'
        staging.mkdir()
        for name, item in lock['files'].items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(item['url'], timeout=120) as response, path.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            if digest(path) != item['sha256']:
                raise ValueError(f'Pi boot download checksum mismatch: {name}')
        (staging / 'boot-notices.txt').write_text(notices(lock))
        (staging / 'rpi5-boot.lock.json').write_bytes(raw)
        hashes = {name: item['sha256'] for name, item in lock['files'].items()}
        hashes['boot-notices.txt'] = digest(staging / 'boot-notices.txt')
        (staging / 'manifest.json').write_text(json.dumps({
            'format': 'hyper.rpi5-boot.v2', 'sha256': hashes}, indent=2) + '\n')
        validate(staging)
        try:
            staging.rename(generation)
        except OSError:
            # A concurrent successful fetch can publish the same generation.
            if not generation.exists():
                raise
            validate(generation)
    return generation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'target/rpi5-boot')
    args = parser.parse_args()
    try:
        print(fetch(args.output).resolve())
    except (OSError, ValueError) as error:
        parser.exit(1, f'fetch-rpi5-boot: {error}\n')


if __name__ == '__main__':
    main()
