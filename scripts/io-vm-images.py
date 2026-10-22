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


def package_payloads(package, platform='qemu', *, require_client_resources=False):
    """Accept a complete external boot generation or a verified OCI import."""
    boot = package / 'boot-artifacts.json'
    if boot.exists():
        metadata = json.loads(bounded_read(boot, MIB))
        if metadata.get('format') != 1 or metadata.get('architecture') != 'aarch64':
            raise ValueError('unsupported boot artifact format or architecture')
        if metadata.get('platform', 'qemu') != platform:
            raise ValueError('boot artifact platform mismatch')
        clients_version = metadata.get('client_table_version', 3)
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
        metadata = json.loads(bounded_read(package / 'oci-manifest.json', MIB))
        clients_version = metadata.get('annotations', {}).get('org.hyper.client-table-version', '3')
        image, initramfs = package / 'Image', package / 'initramfs.cpio.gz'
    if require_client_resources and str(clients_version) != '5':
        raise ValueError('I/O VM package lacks hyper.clients.v5 per-client SCSI/network resources; '
                         'update the pinned appliance or set IO_VM_PACKAGE to a compatible build')
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
        bootargs = ('console=ttyAMA0 earlycon '
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


def deployment_board(board, mode):
    """Retain one board snapshot; diskless qualification cannot require volumes."""
    from board_config import Board
    source = copy.deepcopy(board.source)
    if 'io-vm' not in source:
        raise ValueError('an io-vm boot profile requires an I/O VM board configuration')
    config = source['io-vm']['configuration']
    if mode != 'storage':
        # Standby has no physical network transport.
        # Their reduced snapshot must not inherit a production uplink claim.
        source['io-vm'].pop('network-device', None)
        source['io-vm'].pop('networks', None)
        if source['boot'] == 'qemu-direct':
            # The standalone standby runner uses the MMIO compatibility path;
            # production board boots retain their selected PCI or MMIO device.
            source['io-vm']['io-device'] = {
                'profile': 'virtio-mmio-scsi', 'compatible': 'virtio,mmio',
            }
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
    parser.add_argument('--mode', choices=('storage', 'standby'), default='storage',
                        help='storage deployment or idle backend')
    args = parser.parse_args()
    board = deployment_board(Board.load(args.board), args.mode)
    platform = 'rpi5' if board.source['boot'] == 'rpi5-firmware' else 'qemu'
    image, initramfs = package_payloads(args.package, platform,
                                      require_client_resources=args.mode == 'storage')
    args.output.parent.mkdir(parents=True, exist_ok=True)
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
    entries = [
        ['0644', board.source['io-vm']['image'][1:], str(args.output.resolve())],
        ['0644', 'etc/hyper/board.json', str(snapshot.resolve())],
    ]
    if args.mode == 'storage':
        for name in ('storage.json', 'filesystem.json'):
            entries.append(['0644', 'etc/hyper/' + name,
                            str((args.output.parent / 'board' / name).resolve())])
        entries.append(['0644', 'etc/fstab',
                        str((args.output.parent / 'board/boot-fstab').resolve())])
    write_json(args.output.with_suffix('.entries.json'), entries)


if __name__ == '__main__':
    main()
