#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Assemble a Pi 5 disk from separately built, verified boot inputs."""

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
    if manifest.get('format') != 'hyper.rpi5-boot.v2':
        raise ValueError('unsupported boot package format')
    expected = {'bcm2712-rpi-5-b.dtb', 'bcm2712d0-rpi-5-b.dtb',
                'overlays/bcm2712d0.dtbo', 'overlays/overlay_map.dtb', 'boot-notices.txt', 'sources.tar.gz'}
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
    for name in ('bcm2712-rpi-5-b.dtb', 'bcm2712d0-rpi-5-b.dtb',
                 'overlays/bcm2712d0.dtbo', 'overlays/overlay_map.dtb'):
        if (package / name).read_bytes()[:4] != b'\xd0\x0d\xfe\xed':
            raise ValueError(f'host DTB has an invalid FDT header: {name}')
    return {'host-dtb': package / 'bcm2712-rpi-5-b.dtb',
            'host-dtb-d0': package / 'bcm2712d0-rpi-5-b.dtb',
            'host-overlay-d0': package / 'overlays/bcm2712d0.dtbo',
            'host-overlay-map': package / 'overlays/overlay_map.dtb',
            'boot-notices': package / 'boot-notices.txt'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, help='local override; default downloads pinned official inputs')
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--artifact', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--board', type=Path, default=ROOT / 'boards/rpi5-native.json')
    args = parser.parse_args()
    try:
        package = args.package
        if package is None:
            fetch_spec = importlib.util.spec_from_file_location(
                'fetch_rpi5_boot', ROOT / 'scripts/fetch-rpi5-boot.py')
            fetcher = importlib.util.module_from_spec(fetch_spec)
            fetch_spec.loader.exec_module(fetcher)
            package = fetcher.fetch(ROOT / 'target/rpi5-boot')
        artifacts = boot_artifacts(package)
        artifacts.update(hyper=args.kernel, bootstrap=args.initramfs)
        board = Board.load(args.board)
        if board.source['boot'] != 'rpi5-firmware':
            raise ValueError('Pi 5 image requires the firmware boot profile')
        artifacts = packer.artifact_inputs(
            board, [f'{name}={path}' for name, path in artifacts.items()], args.artifact)
        packer.build(board, artifacts, args.output, replace=args.replace)
        print(f'Pi 5 image: {args.output}')
        print(f'Retain {package}/sources.tar.gz and manifest.json with any distributed image.')
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Pi 5 bring-up: {error}\n')


if __name__ == '__main__':
    main()
