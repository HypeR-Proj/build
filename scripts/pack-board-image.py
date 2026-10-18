#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Build GPT images and refresh generated system volumes without resetting data."""

import argparse
import binascii
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile

from board_config import Board, GPT_ENTRIES, GPT_ENTRY_BYTES, GPT_TABLE_SECTORS, SECTOR
from host_e2fsprogs import ensure_e2fsprogs


def gpt(board):
    entries = bytearray(GPT_ENTRIES * GPT_ENTRY_BYTES)
    for index, part in enumerate(board.partitions):
        # VM containers intentionally have a project type GUID, never a host FS type.
        struct.pack_into('<16s16sQQQ72s', entries, index * GPT_ENTRY_BYTES,
                         part.type_id.bytes_le, part.identifier.bytes_le,
                         part.start, part.end, 0, part.name.encode('utf-16-le'))
    checksum = binascii.crc32(entries)

    def header(current, alternate, table):
        result = bytearray(SECTOR)
        struct.pack_into('<8sIIIIQQQQ16sQIII', result, 0, b'EFI PART', 0x10000, 92, 0, 0,
                         current, alternate, 2 + GPT_TABLE_SECTORS,
                         board.disk_sectors - GPT_TABLE_SECTORS - 2,
                         board.disk_id.bytes_le, table, GPT_ENTRIES, GPT_ENTRY_BYTES, checksum)
        struct.pack_into('<I', result, 16, binascii.crc32(result[:92]))
        return result

    mbr = bytearray(SECTOR)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\2\0', 0xee, b'\xff\xff\xff',
                     1, min(board.disk_sectors - 1, 0xffffffff))
    mbr[510:512] = b'\x55\xaa'
    last = board.disk_sectors - 1
    return [(0, mbr), (1, header(1, last, 2)), (2, entries),
            (last - GPT_TABLE_SECTORS, entries),
            (last, header(last, 1, last - GPT_TABLE_SECTORS))]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_disk(stream, board):
    """Require both GPT copies and the disk extent to match before using offsets."""
    if os.fstat(stream.fileno()).st_size != board.disk_sectors * SECTOR:
        raise ValueError('missing or incompatible board disk; create a new board-image first')
    for sector, expected in gpt(board):
        stream.seek(sector * SECTOR)
        if stream.read(len(expected)) != expected:
            raise ValueError('board configuration does not match the existing disk GPT')


def sparse_copy_bytes(source, destination, remaining):
    """Copy a bounded extent into a fresh zero-filled image, retaining holes."""
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError('source shortened during packing')
        if chunk.count(0) == len(chunk):
            destination.seek(len(chunk), os.SEEK_CUR)
        else:
            destination.write(chunk)
        remaining -= len(chunk)


def sparse_copy(source, destination, offset, capacity):
    """Bound writes to one volume; preserve zero extents in newly created images."""
    remaining = source.stat().st_size
    if remaining > capacity:
        raise ValueError(f'artifact exceeds its partition: {source}')
    with source.open('rb') as stream:
        destination.seek(offset)
        sparse_copy_bytes(stream, destination, remaining)
        if stream.read(1):
            raise ValueError(f'artifact grew during packing: {source}')


def pad_arm64_boot_image(path):
    """Keep firmware followkernel placement beyond the complete runtime image."""
    with path.open('r+b') as image:
        header = image.read(64)
        if len(header) != 64 or header[56:60] != b'ARM\x64':
            raise ValueError('Pi 5 kernel must have a Linux AArch64 Image header')
        memory_size = struct.unpack_from('<Q', header, 16)[0]
        file_size = path.stat().st_size
        if memory_size < file_size or memory_size > 256 * 1024 * 1024:
            raise ValueError('invalid Pi 5 kernel runtime image size')
        # BSS, bootstrap tables and stack are NOLOAD sections. Firmware must
        # reserve them even when it places initramfs using the file length.
        image.truncate(memory_size)


def system_files(root):
    """Snapshot typed entries, modes and links without following host symlinks."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f'system payload must be a directory: {root}')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            files[str(path.relative_to(root))] = {'symlink': os.readlink(path)}
            continue
        if not (path.is_dir() or path.is_file()):
            raise ValueError(f'unsupported system payload entry: {path}')
        if path.is_file():
            files[str(path.relative_to(root))] = {'sha256': digest(path),
                                                   'mode': path.stat().st_mode & 0o777}
    if not files:
        raise ValueError(f'system payload is empty: {root}')
    return files


def system_state(root, board=None):
    return {'files': system_files(root),
            'native': board.source['native'] if board is not None else None}


def system_state_path(output):
    return Path(str(output) + '.system.json')


def check_system_payload(output, root, board=None):
    """A persistent disk must contain this build's executables and DSOs."""
    try:
        previous = json.loads(system_state_path(output).read_text())
    except (OSError, ValueError) as error:
        raise ValueError('split disk has no system payload record; run make to '
                         'refresh its generated system volume') from error
    if not output.is_file() or previous != system_state(root, board):
        raise ValueError('split disk contains a different system payload; run make '
                         'to refresh its generated system volume (disk data is preserved)')


def disk_stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def refresh_system_payload(board, output, root, mkfs_erofs='mkfs.erofs'):
    """Publish an updated EROFS volume while preserving every byte outside it.

    Build a sibling image under a POSIX byte-range lock, which conflicts with
    QEMU's image locks. Failure before publication leaves the old disk intact.
    The payload record follows disk publication: interruption between the two
    leaves a stale record, so the next build safely retries the refresh.
    """
    if output.is_symlink() or not output.is_file():
        raise ValueError('system refresh requires an existing regular disk image, not a symlink')
    with output.open('r+b') as source:
        validate_disk(source, board)
        current = system_state(root, board)
        try:
            previous = json.loads(system_state_path(output).read_text())
        except (OSError, ValueError):
            previous = None
        if previous == current:
            return False
        part = next((part for part in board.partitions if part.content == 'system'), None)
        if part is None or part.image or part.filesystem != 'erofs':
            raise ValueError('automatic system refresh requires a generated EROFS volume; '
                             'use a new output path or an explicit board-rebuild')
        try:
            fcntl.lockf(source, fcntl.LOCK_EX | fcntl.LOCK_NB, 0, 0)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise ValueError('disk image is in use; stop QEMU before refreshing /system') from error
        original = os.fstat(source.fileno())
        validate_disk(source, board)
        with tempfile.TemporaryDirectory(prefix='.board-update-', dir=output.parent) as temporary:
            staging = Path(temporary)
            system_image = staging / 'system.erofs'
            prepare_erofs(root, system_image, mkfs_erofs)
            image = staging / 'disk.img'
            start, end = part.start * SECTOR, (part.end + 1) * SECTOR
            if system_image.stat().st_size > end - start:
                raise ValueError(f'artifact exceeds its partition: {system_image}')
            with image.open('xb') as destination:
                destination.truncate(original.st_size)
                # Never copy the old system extent: holes and unused tail bytes
                # in a smaller replacement must remain zero, not stale content.
                for offset, length in ((0, start), (end, original.st_size - end)):
                    source.seek(offset)
                    destination.seek(offset)
                    sparse_copy_bytes(source, destination, length)
                sparse_copy(system_image, destination, start, end - start)
                os.fchmod(destination.fileno(), stat.S_IMODE(original.st_mode))
                destination.flush()
                os.fsync(destination.fileno())
            if current != system_state(root, board):
                raise ValueError('system payload changed while packing; retry')
            if (disk_stamp(os.fstat(source.fileno())) != disk_stamp(original)
                    or disk_stamp(output.stat()) != disk_stamp(original)):
                raise ValueError('disk image changed during system refresh; retry with the disk idle')
            state = staging / 'system.json'
            with state.open('w') as stream:
                stream.write(json.dumps(current, indent=2, sort_keys=True) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(image, output)
            state.replace(system_state_path(output))
    print(f'{output}: refreshed {part.name}; persistent data and guest volumes preserved')
    return True


def prepare_payload(board, artifacts, directory, system_root=None):
    required = set(board.source['files'].values()) | {
        part.image for part in board.partitions if part.image}
    if artifacts.keys() != required:
        raise ValueError(f'artifact keys differ: missing={required - artifacts.keys()}, '
                         f'unknown={artifacts.keys() - required}')
    for key, path in artifacts.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'missing or empty {key} artifact: {path}')
    for part in board.partitions:
        if part.image and artifacts[part.image].stat().st_size != part.sectors * SECTOR and not part.native:
            # In particular, never silently enlarge a guest GPT disk and strand its
            # backup header in the middle. Resize with the guest's own image tooling.
            raise ValueError(f'{part.image}: disk image must exactly match the configured volume size')
    for target, key in board.source['files'].items():
        destination = directory / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifacts[key], destination)
    vms = board.vms()
    for filename, content in [('board.json', board.source), ('partitions.json', board.partition_manifest()),
                              ('vms.json', vms)]:
        (directory / filename).write_text(json.dumps(content, indent=2) + '\n')
    if board.source['boot'] == 'rpi5-firmware':
        pad_arm64_boot_image(directory / 'hyper.img')
        for filename in ('config.txt', 'cmdline.txt'):
            if filename in board.source['files']:
                raise ValueError(f'{filename} is generated by the boot profile')
        # The RP1 assignment profile validates and inherits the firmware's
        # configured PCIe link. A reset would discard that handoff state.
        rp1_handoff = ('pciex4_reset=0\n'
                       if 'network-device' in board.source['io-vm'] else '')
        (directory / 'config.txt').write_text(
            '[pi5]\nkernel=hyper.img\n'
            'initramfs bootstrap.cpio followkernel\ncmdline=cmdline.txt\n'
            'uart_2ndstage=1\ncamera_auto_detect=0\ndisplay_auto_detect=0\n'
            + rp1_handoff + '[all]\n')
        (directory / 'cmdline.txt').write_text('earlycon=pl011,0x107d001000,115200n8 loglevel=7\n')

def _find_executable(program, directories=()):
    """Find an executable in PATH and then in additional directories."""
    resolved = shutil.which(program)
    if resolved:
        return resolved

    for directory in directories:
        path = Path(directory) / program
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    return None

def image_tool(program, default, formula):
    # 1. Respect PATH first.
    resolved = _find_executable(program)
    if resolved:
        return resolved

    # 2. Search common Linux sbin locations.
    linux_sbin_dirs = (
        "/usr/local/sbin",
        "/usr/sbin",
        "/sbin",
    )
    resolved = _find_executable(program, linux_sbin_dirs)
    if resolved:
        return resolved

    # 3. Search Homebrew's bin/sbin.
    #
    # Only use brew when looking for the default program, preserving the
    # existing behavior where an explicitly overridden executable remains
    # authoritative.
    brew = shutil.which("brew") if program == default else None
    if brew:
        try:
            prefix = subprocess.run(
                [brew, "--prefix", formula],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()

            if prefix and Path(prefix).is_absolute():
                resolved = _find_executable(
                    program,
                    (
                        Path(prefix) / "bin",
                        Path(prefix) / "sbin",
                    ),
                )
                if resolved:
                    return resolved

        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            pass

    # MacPorts' e2fsprogs pulls in ossp-uuid, which conflicts with the libuuid
    # required by erofs-utils. Build only our ext4 tools with private libraries.
    if program == default and formula == 'e2fsprogs' and program in ('mke2fs', 'debugfs'):
        return str(ensure_e2fsprogs() / program)

    raise ValueError(
        f"{program} was not found; install {formula} "
        "or provide its executable path"
    )


def prepare_erofs(root, image, mkfs_erofs):
    tool = image_tool(mkfs_erofs, 'mkfs.erofs', 'erofs-utils')
    # erofs-utils 1.7 accepts UUIDs, but not the newer "clear" alias.
    subprocess.run([tool, '-b4096', '-x-1', '-T0',
                    '-U00000000-0000-0000-0000-000000000000', '--all-root',
                    str(image), str(root)], check=True)


def prepare_native_volumes(board, staging, system_root, mkfs, mcopy, mkfs_erofs, data_root=None):
    """Format only requested Native volumes; guest images remain opaque."""
    images = {}
    system_part = next((part for part in board.partitions
                        if part.content == 'system'), None)
    if system_root is not None and (system_part is None or system_part.image):
        raise ValueError('--system-root requires a generated Native system volume')
    if system_part is not None and not system_part.image and system_root is None:
        raise ValueError('generated system volume requires --system-root')
    for part in board.partitions:
        if not part.native or part.image:
            continue
        image = staging / (part.identifier.hex + '.fs')
        root = {'data': data_root, 'system': system_root}.get(part.content)
        if part.content == 'data' and root is None:
            continue
        if part.filesystem == 'erofs':
            prepare_erofs(root, image, mkfs_erofs)
        elif part.filesystem == 'fat':
            with image.open('xb') as stream:
                stream.truncate(part.sectors * SECTOR)
            subprocess.run([mkfs, '--invariant', '-F', '32', '-S', str(SECTOR),
                            '-n', 'HYPER', str(image)], check=True)
            if root is not None:
                if any(path.is_symlink() for path in root.rglob('*')):
                    raise ValueError('FAT system images cannot encode symbolic links; use EROFS')
                subprocess.run([mcopy, '-i', str(image), '-s',
                                *(str(item) for item in sorted(root.iterdir())), '::/'], check=True)
        else:
            raise ValueError(f'unsupported Native image format: {part.filesystem}')
        if image.stat().st_size > part.sectors * SECTOR:
            raise ValueError(f'{part.name}: filesystem exceeds configured volume capacity')
        images[part.name] = image
    return images


def build(board, artifacts, output, mkfs='mkfs.fat', mcopy='mcopy', *, replace=False,
          system_root=None, mkfs_erofs='mkfs.erofs'):
    if not replace and (output.exists() or output.is_symlink()):
        raise ValueError('refusing to overwrite an existing image; choose a new output path')
    mkfs = image_tool(mkfs, 'mkfs.fat', 'dosfstools')
    mcopy = image_tool(mcopy, 'mcopy', 'mtools')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.board-image-', dir=output.parent) as temporary:
        staging = Path(temporary)
        payload = staging / 'payload'
        payload.mkdir()
        checksums = {key: digest(path) for key, path in artifacts.items()}
        system_checksums = system_state(system_root, board) if system_root is not None else None
        prepare_payload(board, artifacts, payload, system_root)
        native_images = prepare_native_volumes(board, staging, system_root,
                                               mkfs, mcopy, mkfs_erofs, payload)
        image = staging / 'disk.img'
        with image.open('xb') as stream:
            stream.truncate(board.disk_sectors * SECTOR)
            for sector, contents in gpt(board):
                stream.seek(sector * SECTOR)
                stream.write(contents)
            for part in board.partitions:
                source = artifacts.get(part.image) if part.image else native_images.get(part.name)
                if source is not None:
                    sparse_copy(source, stream, part.start * SECTOR, part.sectors * SECTOR)
            stream.flush()
            os.fsync(stream.fileno())
        if checksums != {key: digest(path) for key, path in artifacts.items()}:
            raise ValueError('artifacts changed while packing; retry')
        if system_root is not None and system_checksums != system_state(system_root, board):
            raise ValueError('system payload changed while packing; retry')
        state = staging / 'system.json'
        if system_checksums is not None:
            state.write_text(json.dumps(system_checksums, indent=2, sort_keys=True) + '\n')
        # Keep the old disk intact until the complete replacement is ready.
        if replace:
            os.replace(image, output)
        else:
            # Atomic no-replace publication, including races with another packer.
            os.link(image, output)
        if system_checksums is not None:
            state.replace(system_state_path(output))
        else:
            system_state_path(output).unlink(missing_ok=True)
    print(f'{output}: {board.disk_sectors * SECTOR} bytes; {len(board.partitions)} volumes')


def artifact_inputs(board, defaults, explicit):
    """Select build defaults by policy, but reject typos in explicit inputs."""
    def pairs(items):
        result = {}
        for item in items:
            key, separator, path = item.partition('=')
            if not separator or not key or not path or key in result:
                raise ValueError('artifacts must be unique NAME=PATH pairs')
            result[key] = Path(path)
        return result

    required = set(board.source['files'].values()) | {
        part.image for part in board.partitions if part.image}
    selected = {key: path for key, path in pairs(defaults).items() if key in required}
    overrides = pairs(explicit)
    if overrides.keys() - required:
        raise ValueError(f'unknown explicit artifacts: {overrides.keys() - required}')
    selected.update(overrides)
    if selected.keys() != required:
        raise ValueError(f'missing artifacts: {required - selected.keys()}')
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--artifact', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--default-artifact', action='append', default=[], metavar='NAME=PATH',
                        help='build default, selected only when referenced by board policy')
    parser.add_argument('--plan', action='store_true', help='validate and print volume layout only')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--replace', action='store_true',
                      help='atomically replace the output, resetting all disk data')
    parser.add_argument('--mkfs-erofs', default='mkfs.erofs')
    parser.add_argument('--mkfs', default='mkfs.fat')
    parser.add_argument('--mcopy', default='mcopy')
    parser.add_argument('--system-root', type=Path,
                        help='Native /system payload for its independent disk volume')
    mode.add_argument('--check-system-payload', action='store_true',
                      help='check that an existing disk contains the staged Native /system build')
    mode.add_argument('--refresh-system-payload', action='store_true',
                      help='update a generated EROFS system volume, preserving all other disk bytes')
    args = parser.parse_args()
    try:
        board = Board.load(args.board)
        if args.plan:
            print(json.dumps(board.partition_manifest(), indent=2))
            return
        if args.output is None:
            raise ValueError('--output is required when building an image')
        if args.check_system_payload:
            if args.system_root is None:
                raise ValueError('--check-system-payload requires --system-root')
            check_system_payload(args.output, args.system_root, board)
            return
        if args.refresh_system_payload:
            if args.system_root is None:
                raise ValueError('--refresh-system-payload requires --system-root')
            refresh_system_payload(board, args.output, args.system_root, args.mkfs_erofs)
            return
        artifacts = artifact_inputs(board, args.default_artifact, args.artifact)
        build(board, artifacts, args.output, args.mkfs, args.mcopy, replace=args.replace,
              system_root=args.system_root, mkfs_erofs=args.mkfs_erofs)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'board image: {error}\n')


if __name__ == '__main__':
    main()
