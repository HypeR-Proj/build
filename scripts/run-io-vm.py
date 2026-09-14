#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Run the ordinary Native service image with a persistent QEMU storage disk."""

import argparse
import os
from pathlib import Path


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
    args = parser.parse_args()
    prepare_disk(args.disk, 64 * 1024 * 1024)
    command = [args.qemu, '-machine', os.environ.get('QEMU_MACHINE', 'virt,virtualization=on,gic-version=3'),
               '-cpu', os.environ.get('QEMU_CPU', 'max'), '-smp', os.environ.get('QEMU_CPUS', '4'),
               '-m', os.environ.get('QEMU_MEMORY', '512M'), '-nodefaults', '-display', 'none',
               '-serial', 'stdio', '-monitor', 'none', '-nic', 'none', '-no-reboot',
               '-kernel', str(args.image), '-initrd', str(args.initramfs),
               '-append', os.environ.get('QEMU_BOOTARGS', 'earlycon=pl011,mmio32,0x09000000'),
               '-global', 'virtio-mmio.force-legacy=false',
               '-drive', 'if=none,id=physicaldisk,format=raw,file='
               + str(args.disk.resolve()).replace(',', ',,') + ',cache=writeback',
               '-device', 'virtio-scsi-device,id=physicalscsi,iommu_platform=on',
               '-device', 'scsi-hd,drive=physicaldisk,bus=physicalscsi.0,scsi-id=0,lun=0']
    # Replace the launcher: terminal input, signals, and QEMU lifetime remain
    # exactly those of an interactive `make run` invocation.
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
