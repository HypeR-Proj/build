#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Create a disposable Alpine ext4 root disk sized by board policy."""
import argparse
import importlib.util
import os
import stat
from pathlib import Path
import subprocess
import tarfile
import tempfile
from board_config import Board

spec = importlib.util.spec_from_file_location('pack_board_image', Path(__file__).with_name('pack-board-image.py'))
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


def build(board, archive, output):
    volumes = [part for part in board.partitions if part.image == 'alpine-rootfs']
    if not volumes:
        return
    sizes = {part.sectors * 512 for part in volumes}
    if len(sizes) != 1:
        raise ValueError('volumes sharing alpine-rootfs must have the same size')
    mkfs = packer.image_tool('mke2fs', 'mke2fs', 'e2fsprogs')
    debugfs = packer.image_tool('debugfs', 'debugfs', 'e2fsprogs')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.guest-disk-') as temporary:
        temporary = Path(temporary)
        root = temporary / 'root'
        root.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(root, filter='data')
        disk = temporary / 'root.ext4'
        with disk.open('wb') as stream:
            stream.truncate(sizes.pop())
        subprocess.run([mkfs, '-q', '-t', 'ext4', '-F', '-L', 'alpine-root',
                        '-E', 'lazy_itable_init=0,lazy_journal_init=0,root_owner=0:0',
                        '-d', str(root), str(disk)], check=True)
        # mke2fs -d copies host ownership. Normalize every inode to the archive
        # ownership without requiring root, fakeroot, or libarchive support.
        commands = temporary / 'ownership.txt'
        with tarfile.open(archive) as source, commands.open('w') as script:
            for item in source:
                path = '/' + item.name.removeprefix('./')
                if any(c in path for c in '\n\r"\\'):
                    raise ValueError('unsupported rootfs filename')
                kind = stat.S_IFDIR if item.isdir() else stat.S_IFLNK if item.issym() else stat.S_IFREG
                for field, value in (('uid', item.uid), ('gid', item.gid),
                                     ('mode', kind | item.mode)):
                    script.write(f'set_inode_field "{path}" {field} {value}\n')
        subprocess.run([debugfs, '-w', '-f', str(commands), str(disk)],
                       check=True, stdout=subprocess.DEVNULL)
        os.replace(disk, output)
    print(f'Prepared Alpine ext4 root disk: {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('board', 'rootfs', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    build(Board.load(args.board), args.rootfs, args.output)


if __name__ == '__main__':
    main()
