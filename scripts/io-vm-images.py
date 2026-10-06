#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate an I/O VM appliance and compose HypeR-owned guest FIT images."""

import argparse
import copy
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
    definitions = []
    for role in ('io', 'business'):
        bootargs = ('console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 '
                    f'rdinit=/init loglevel=7 hyper.role={role} hyper.test={test}')
        subprocess.run([str(fit_pack), str(output / f'{role}.itb'), 'arm64',
                        str(image), '0x40200000', '0x40200000', str(initramfs)], check=True)
        definitions.append({'name': role, 'image': f'/vm/{role}.itb',
                            'configuration': {'memory-bytes': 64 * MIB, 'vcpus': 1,
                                              'bootargs': bootargs}})
    write_json(output / 'io-vms.json', {'format': 'hyper.vm-config', 'virtual-machines': definitions})


def write_json(path, value):
    contents = json.dumps(value, indent=2) + '\n'
    if not path.exists() or path.read_text() != contents:
        path.write_text(contents)


def bringup_config(output, board):
    """Boot the appliance as a supervised VM without physical-device authority."""
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'app/init/config/services-with-vms.json').read_text())
    manifest['services'] = [service for service in manifest['services']
                            if service['name'] != 'io-runtime']
    manifest['virtual-machines'] = {'config': '/etc/hyper/vms.json'}
    resident = board.source['io-vm']
    vms = {'format': 'hyper.vm-config', 'virtual-machines': [
        {'name': resident['name'], 'image': resident['image'], 'autostart': False,
         'configuration': resident['configuration']}]}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('services.json', manifest), ('vms.json', vms)]:
        (output / name).write_text(json.dumps(value, indent=2) + '\n')


def deployment_board(board, mode):
    """Retain one board snapshot; diskless qualification cannot require volumes."""
    from board_config import Board
    source = copy.deepcopy(board.source)
    config = source['io-vm']['configuration']
    if mode != 'storage':
        # Standby and diskless bring-up have no physical network transport.
        # Their reduced snapshot must not inherit a production uplink claim.
        source['io-vm'].pop('network-device', None)
        source['io-vm'].pop('networks', None)
        for vm in source['virtual-machines']:
            vm.pop('network', None)
        arguments = re.sub(r'(?<!\S)hyper\.(?:mode|volumes)=\S+\s*', '', config['bootargs'])
        config['bootargs'] = (arguments.rstrip() + f' hyper.mode={mode}').lstrip()
    return Board.parse(source)


def main():
    from board_config import Board
    from board_bootstrap import linux_overlay, stage
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--fit-pack', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--board', type=Path, required=True,
                        help='board JSON owning the I/O VM configuration')
    parser.add_argument('--mode', choices=('storage', 'standby', 'bringup'), default='storage',
                        help='storage deployment, idle backend, or diskless supervised bring-up')
    args = parser.parse_args()
    board = deployment_board(Board.load(args.board), args.mode)
    platform = 'rpi5' if board.source['boot'] == 'rpi5-firmware' else 'qemu'
    image, initramfs = package_payloads(args.package, platform)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == 'bringup':
        bringup_config(args.output.parent / 'bringup', board)
    if args.mode == 'storage':
        stage(board, Path(__file__).resolve().parents[1], args.output.parent / 'board')
        contents = linux_overlay(board, bounded_read(initramfs, 8 * MIB))
        initramfs = args.output.parent / 'io-board.cpio.gz'
        if not initramfs.exists() or initramfs.read_bytes() != contents:
            initramfs.write_bytes(contents)
    subprocess.run([str(args.fit_pack), str(args.output), 'arm64',
                    str(image), '0x40200000', '0x40200000', str(initramfs)], check=True)
    snapshot = args.output.with_suffix('.board.json')
    write_json(snapshot, board.source)
    # Guest paths remain data all the way into the packer, never shell fragments.
    write_json(args.output.with_suffix('.entries.json'), [
        ['0644', board.source['io-vm']['image'][1:], str(args.output.resolve())],
        ['0644', 'etc/hyper/board.json', str(snapshot.resolve())],
    ])


if __name__ == '__main__':
    main()
