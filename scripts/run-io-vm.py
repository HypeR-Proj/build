#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Run the ordinary Native service image with a persistent QEMU storage disk."""

import argparse
import os
import sys
from pathlib import Path
import importlib.util


def validate_board_disk(path, configuration):
    """A deployment boot may never silently substitute an empty scratch disk."""
    from board_config import Board, SECTOR
    spec = importlib.util.spec_from_file_location(
        'hyper_board_packer', Path(__file__).with_name('pack-board-image.py'))
    packer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packer)
    board = Board.load(configuration)
    if board.source['boot'] != 'qemu-direct':
        raise ValueError('the interactive QEMU launcher requires qemu-direct boot')
    if not path.is_file() or path.stat().st_size != board.disk_sectors * SECTOR:
        raise ValueError('missing or incompatible board disk; create a new board-image first')
    with path.open('rb') as stream:
        for sector, expected in packer.gpt(board):
            stream.seek(sector * SECTOR)
            if stream.read(len(expected)) != expected:
                raise ValueError('board configuration does not match the existing disk GPT')


def prepare_disk(path, size):
    """Create once without truncating an existing user-selected disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.truncate(size)
    except FileExistsError:
        if not path.is_file() or path.stat().st_size < 8 * 1024 * 1024:
            raise ValueError('I/O VM disk must be a regular raw image of at least 8 MiB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--disk', type=Path, required=True)
    parser.add_argument('--board', type=Path)
    parser.add_argument('--dtb', type=Path, default=os.environ.get('QEMU_DTB'))
    args = parser.parse_args()
    for path in (args.image, args.initramfs, args.disk):
        if not path.is_file():
            parser.error(f'missing artifact {path}; run make first')
    if args.board:
        validate_board_disk(args.disk, args.board)
    else:
        if args.disk.stat().st_size < 8 * 1024 * 1024:
            parser.error('I/O VM disk must be at least 8 MiB')
    command = [args.qemu, '-machine', os.environ.get('QEMU_MACHINE', 'virt,virtualization=on,gic-version=3'),
               '-cpu', os.environ.get('QEMU_CPU', 'max'), '-smp', os.environ.get('QEMU_CPUS', '4'),
               '-m', os.environ.get('QEMU_MEMORY', '1G'), '-nodefaults', '-display', 'none',
               '-nic', 'none', '-no-reboot',
               '-kernel', str(args.image), '-initrd', str(args.initramfs),
               '-append', os.environ.get('QEMU_BOOTARGS', 'earlycon=pl011,mmio32,0x09000000'),
               '-global', 'virtio-mmio.force-legacy=false',
               '-drive', 'if=none,id=physicaldisk,format=raw,file='
               + str(args.disk.resolve()).replace(',', ',,') + ',cache=writeback',
               '-device', 'virtio-scsi-device,id=physicalscsi,iommu_platform=on',
               '-device', 'scsi-hd,drive=physicaldisk,bus=physicalscsi.0,scsi-id=0,lun=0']
    # The monitor's escape processing belongs to interactive terminals. Keep
    # piped automation on the dedicated serial backend for lossless bursts.
    if sys.stdin.isatty():
        command.extend(['-serial', 'mon:stdio'])
    else:
        command.extend(['-serial', 'stdio', '-monitor', 'none'])
    if args.dtb:
        command.extend(['-dtb', str(args.dtb)])
    # Replace the launcher: terminal input, signals, and QEMU lifetime remain
    # exactly those of an interactive `make run` invocation.
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
