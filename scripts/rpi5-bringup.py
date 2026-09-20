#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Assemble a Native-only Pi 5 disk from separately built, verified boot inputs."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from board_config import Board, unique_object

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pack_board', ROOT / 'scripts/pack-board-image.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


def boot_artifacts(package):
    manifest = json.loads((package / 'manifest.json').read_text(), object_pairs_hook=unique_object)
    if manifest.get('format') != 'hyper.rpi5-boot.v1':
        raise ValueError('unsupported boot package format')
    expected = {'bl31.bin', 'bcm2712-rpi-5-b.dtb', 'boot-notices.txt', 'sources.tar.gz'}
    hashes = manifest.get('sha256', {})
    if set(hashes) != expected:
        raise ValueError('boot package must include firmware, DTB, notices and corresponding sources')
    for name, checksum in hashes.items():
        path = package / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'missing or empty boot package file: {name}')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != checksum:
            raise ValueError(f'boot package checksum mismatch: {name}')
    if (package / 'bcm2712-rpi-5-b.dtb').read_bytes()[:4] != b'\xd0\x0d\xfe\xed':
        raise ValueError('host DTB has an invalid FDT header')
    return {'tfa': package / 'bl31.bin', 'host-dtb': package / 'bcm2712-rpi-5-b.dtb',
            'boot-notices': package / 'boot-notices.txt'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    try:
        artifacts = boot_artifacts(args.package)
        artifacts.update(hyper=args.kernel, bootstrap=args.initramfs)
        board = Board.load(ROOT / 'boards/rpi5-native.json')
        packer.build(board, artifacts, args.output, replace=args.replace)
        print(f'Pi 5 Native bring-up image: {args.output}')
        print(f'Retain {args.package}/sources.tar.gz and manifest.json with any distributed image.')
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Pi 5 bring-up: {error}\n')


if __name__ == '__main__':
    main()
