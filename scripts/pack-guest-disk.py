#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Create a disposable Alpine ext4 root disk sized by board policy."""
import argparse
import fcntl
import json
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
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_name(output.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _build(board, archive, output)


def publish(template, output):
    # Each caller receives a fresh disposable disk. Only the immutable template
    # is cached; guest writes are never mistaken for reusable build outputs.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.guest-copy-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), template.stat().st_mode & 0o777)
            stream.truncate(template.stat().st_size)
            packer.sparse_copy(template, stream, 0, template.stat().st_size)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def _build(board, archive, output):
    volumes = [part for part in board.partitions if part.image == 'alpine-rootfs']
    if not volumes:
        return
    sizes = {part.sectors * 512 for part in volumes}
    if len(sizes) != 1:
        raise ValueError('volumes sharing alpine-rootfs must have the same size')
    mkfs = packer.image_tool('mke2fs', 'mke2fs', 'e2fsprogs')
    debugfs = packer.image_tool('debugfs', 'debugfs', 'e2fsprogs')
    output.parent.mkdir(parents=True, exist_ok=True)
    size = sizes.pop()
    requested = {'archive': packer.digest(archive), 'size': size, 'builder': packer.digest(Path(__file__)),
                 'mkfs': packer.digest(Path(mkfs)), 'debugfs': packer.digest(Path(debugfs))}
    template = output.with_name(output.name + '.template')
    receipt = output.with_name(output.name + '.state.json')
    try:
        saved = json.loads(receipt.read_bytes())
        if saved['inputs'] == requested and saved['template'] == packer.digest(template):
            publish(template, output)
            print(f'Restored disposable Alpine disk from cached template: {output}')
            return
    except (OSError, ValueError, KeyError):
        pass
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.guest-disk-') as temporary:
        temporary = Path(temporary)
        root = temporary / 'root'
        root.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(root, filter='data')
        disk = temporary / 'root.ext4'
        with disk.open('wb') as stream:
            stream.truncate(size)
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
        if packer.digest(archive) != requested['archive']:
            raise ValueError('guest rootfs input changed during construction; retry')
        os.replace(disk, template)
        receipt.write_text(json.dumps({'inputs': requested, 'template': packer.digest(template)}) + '\n')
        publish(template, output)
    print(f'Prepared Alpine ext4 root disk: {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('board', 'rootfs', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    build(Board.load(args.board), args.rootfs, args.output)


if __name__ == '__main__':
    main()
