#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate an I/O VM appliance and compose HypeR-owned guest FIT images."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import zlib

MIB = 1024 * 1024

def bounded_read(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError(f'empty or oversized artifact: {path}')
    return data


def package_payloads(package, platform='qemu'):
    """Accept a complete external boot generation or a verified OCI import."""
    boot = package / 'boot-artifacts.json'
    if boot.exists():
        metadata = json.loads(bounded_read(boot, MIB))
        if metadata.get('format') != 1 or metadata.get('architecture') != 'aarch64':
            raise ValueError('unsupported boot artifact format or architecture')
        if metadata.get('platform', 'qemu') != platform:
            raise ValueError('boot artifact platform mismatch')
        paths = []
        for field, pattern, limit in (
                ('kernel', r'Image-([0-9a-f]{64})', 32 * MIB),
                ('initramfs', r'initramfs-([0-9a-f]{64})\.cpio\.gz', 8 * MIB)):
            match = re.fullmatch(pattern, metadata[field])
            if match is None:
                raise ValueError('invalid content-addressed boot filename')
            path = package / metadata[field]
            data = bounded_read(path, limit)
            if hashlib.sha256(data).hexdigest() != match[1]:
                raise ValueError(f'boot artifact checksum mismatch: {field}')
            paths.append(path)
        image, initramfs = paths
    else:
        # Reuse the importer contract rather than independently interpreting
        # its layer names and source-material requirements.
        source = Path(__file__).with_name('fetch-io-vm.py')
        spec = importlib.util.spec_from_file_location('hyper_io_package', source)
        importer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(importer)
        layers = importer.validate_manifest(package / 'oci-manifest.json',
                                            'sha256:' + package.name, platform)
        importer.validate_runtime(package, layers)
        image, initramfs = package / 'Image', package / 'initramfs.cpio.gz'
    if bounded_read(image, 32 * MIB)[56:60] != b'ARM\x64':
        raise ValueError('not an AArch64 Linux Image')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    expanded = decoder.decompress(bounded_read(initramfs, 8 * MIB), 32 * MIB + 1)
    if (len(expanded) > 32 * MIB or not decoder.eof or decoder.unused_data
            or not expanded.startswith(b'070701')):
        raise ValueError('invalid complete initramfs')
    return image.resolve(), initramfs.resolve()


def prepare(package, fit_pack, output, test="basic"):
    image, initramfs = package_payloads(package)
    output.mkdir(parents=True, exist_ok=True)
    for role in ('io', 'business'):
        bootargs = ('console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 '
                    f'rdinit=/init loglevel=7 hyper.role={role} hyper.test={test}')
        subprocess.run([str(fit_pack), str(output / f'{role}.itb'), 'arm64',
                        str(64 * MIB), '1', str(image), '0x40200000', '0x40200000',
                        str(initramfs), bootargs], check=True)



def bringup_config(output):
    """Boot the appliance as a supervised VM without physical-device authority."""
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'app/init/config/services.json').read_text())
    manifest['services'] = [service for service in manifest['services']
                            if service['name'] != 'io-runtime']
    manifest['virtual-machines'] = {'config': '/etc/hyper/vms.json'}
    vms = {'format': 'hyper.vm-config', 'virtual-machines': [
        {'name': 'io-bringup', 'image': '/vm/io.itb', 'autostart': False}]}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('services.json', manifest), ('vms.json', vms)]:
        (output / name).write_text(json.dumps(value, indent=2) + '\n')


def main():
    from board_config import Board
    from board_bootstrap import linux_overlay, stage
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--fit-pack', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--board', type=Path)
    parser.add_argument('--bringup', action='store_true', help='diskless supervised VM, no storage service')
    parser.add_argument('--platform', choices=('qemu', 'rpi5'))
    args = parser.parse_args()
    if args.bringup and args.board:
        parser.error('--bringup cannot accept a storage board configuration')
    board = Board.load(args.board) if args.board else None
    platform = 'rpi5' if board and board.source['boot'] == 'rpi5-firmware' else 'qemu'
    if args.platform:
        if board and args.platform != platform:
            parser.error('--platform differs from board')
        platform = args.platform
    image, initramfs = package_payloads(args.package, platform)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    arguments = ('console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 rdinit=/init '
                 'loglevel=4 hyper.role=io hyper.mode=standby')
    if args.bringup:
        arguments = arguments.replace('hyper.mode=standby', 'hyper.mode=bringup')
        bringup_config(args.output.parent / 'bringup')
    if board:
        stage(board, Path(__file__).resolve().parents[1], args.output.parent / 'board')
        contents = linux_overlay(board, bounded_read(initramfs, 8 * MIB))
        initramfs = args.output.parent / 'io-board.cpio.gz'
        if not initramfs.exists() or initramfs.read_bytes() != contents:
            initramfs.write_bytes(contents)
        arguments += ' hyper.volumes=required'
    subprocess.run([str(args.fit_pack), str(args.output), 'arm64', str(64 * MIB), '1',
                    str(image), '0x40200000', '0x40200000', str(initramfs),
                    arguments], check=True)


if __name__ == '__main__':
    main()
