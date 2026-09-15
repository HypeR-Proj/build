# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Versioned deployment policy; hardware addresses remain in firmware/guest DTBs."""

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
import uuid

MIB = 1024 * 1024
SECTOR = 512
ALIGN_SECTORS = MIB // SECTOR
GPT_ENTRIES = 128
GPT_ENTRY_BYTES = 128
GPT_TABLE_SECTORS = GPT_ENTRIES * GPT_ENTRY_BYTES // SECTOR
ESP = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b')
# Opaque VM disks must not be advertised as host Linux filesystems or LVM PVs.
VM_DISK = uuid.UUID('a6edb737-452f-4a43-bd9f-bc04aa5323cf')
_NAME = re.compile(r'[a-z][a-z0-9-]{0,30}\Z')
_LICENSE = {'SPDX-FileCopyrightText', 'SPDX-License-Identifier'}


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError('expected a JSON object')
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional) - _LICENSE
    if missing or extra:
        raise ValueError(f'invalid configuration keys: missing={sorted(missing)}, unknown={sorted(extra)}')


def name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f'invalid deployment name: {value!r}')
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'expected integer in [{minimum}, {maximum}], got {value!r}')
    return value


def relative_path(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or any(ord(c) < 32 or c in ':*?"<>|' for c in value)):
        raise ValueError('invalid FAT payload path')
    parts = value.split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in parts):
        raise ValueError('payload paths must be canonical relative paths')
    return str(PurePosixPath(value))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


@dataclass(frozen=True)
class Partition:
    name: str
    start: int
    sectors: int
    identifier: uuid.UUID
    type_id: uuid.UUID
    owner: str
    image: str | None = None

    @property
    def end(self):
        return self.start + self.sectors - 1

    def manifest(self):
        return {'name': self.name, 'partuuid': str(self.identifier),
                'sectors': self.sectors, 'sector-size': SECTOR, 'owner': self.owner,
                'mapper': f'hyper-{self.name}'}


@dataclass(frozen=True)
class Board:
    source: dict
    partitions: tuple[Partition, ...]
    disk_id: uuid.UUID
    disk_sectors: int

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as stream:
            document = stream.read(1024 * 1024 + 1)
        if len(document) > 1024 * 1024:
            raise ValueError('board configuration exceeds 1 MiB')
        return cls.parse(json.loads(document, object_pairs_hook=unique_object))

    @classmethod
    def parse(cls, data):
        keys(data, ('format', 'board', 'architecture', 'boot', 'disk', 'files', 'virtual-machines', 'io-device'))
        if data['format'] != 'hyper.board.v1' or data['architecture'] != 'aarch64':
            raise ValueError('unsupported board format or architecture')
        name(data['board'])
        if data['boot'] not in ('qemu-direct', 'rpi5-tfa'):
            raise ValueError('unsupported boot chain')
        selector = data['io-device']
        keys(selector, ('profile',), ('compatible', 'path'))
        if selector['profile'] not in ('virtio-mmio-scsi', 'bcm2712-sdhci'):
            raise ValueError('unsupported assignment profile')
        identities = [key for key in ('compatible', 'path') if key in selector]
        if len(identities) != 1:
            raise ValueError('device requires exactly one firmware identity')
        identity = selector[identities[0]]
        if not isinstance(identity, str) or not identity or len(identity.encode()) > 512 or any(ord(c) <= 32 or ord(c) == 127 for c in identity):
            raise ValueError('invalid firmware identity')
        if identities[0] == 'path' and (not identity.startswith('/') or any(part in ('', '.', '..') for part in identity.split('/')[1:])):
            raise ValueError('firmware path must be canonical and absolute')
        keys(data['disk'], ('uuid', 'config-mib'))
        disk_id = uuid.UUID(data['disk']['uuid'])
        if disk_id.int == 0:
            raise ValueError('disk UUID must not be nil')
        config_size = integer(data['disk']['config-mib'], 64, 32768) * MIB // SECTOR
        partitions = [Partition('config', ALIGN_SECTORS, config_size,
                                uuid.uuid5(disk_id, 'config'), ESP, 'hyper')]
        if not isinstance(data['files'], dict):
            raise ValueError('files must map destination paths to artifact names')
        required_files = {'hyper.img'}
        if data['boot'] == 'rpi5-tfa':
            required_files |= {'bootstrap.cpio', 'bl31.bin', 'bcm2712-rpi-5-b.dtb'}
        if not required_files <= data['files'].keys():
            raise ValueError('missing boot profile payloads')
        destinations = set()
        for destination, artifact in data['files'].items():
            destination = relative_path(destination).casefold()
            if destination in destinations or destination in ('board.json', 'vms.json', 'volumes.json', 'config.txt', 'cmdline.txt'):
                raise ValueError('duplicate or reserved deployment path')
            destinations.add(destination)
            name(artifact)
        # Files and directories share a case-insensitive FAT namespace.
        for destination in destinations:
            parent = PurePosixPath(destination).parent
            while str(parent) != '.':
                if str(parent) in destinations:
                    raise ValueError('payload file shadows a directory')
                parent = parent.parent
        vms = data['virtual-machines']
        if not isinstance(vms, list) or len(vms) > 8:
            raise ValueError('too many VMs or invalid VM list')
        seen = {'config'}
        for vm in vms:
            keys(vm, ('name', 'image', 'autostart', 'disk-mib'), ('disk-image',))
            vm_name = name(vm['name'])
            if vm_name in seen:
                raise ValueError('duplicate or reserved VM name')
            seen.add(vm_name)
            image = relative_path(vm['image'])
            if image not in data['files'] or not image.endswith('.itb'):
                raise ValueError('VM image must name an explicitly packaged ITB')
            if type(vm['autostart']) is not bool:
                raise ValueError('autostart must be boolean')
            size = integer(vm['disk-mib'], 8, 16 * 1024 * 1024) * MIB // SECTOR
            source = name(vm['disk-image']) if 'disk-image' in vm else None
            start = partitions[-1].end + 1
            partitions.append(Partition(vm_name, start, size, uuid.uuid5(disk_id, vm_name),
                                        VM_DISK, vm_name, source))
        end = partitions[-1].end + 1 + GPT_TABLE_SECTORS + 1
        total = (end + ALIGN_SECTORS - 1) // ALIGN_SECTORS * ALIGN_SECTORS
        return cls(data, tuple(partitions), disk_id, total)

    def volumes(self):
        return {'format': 'hyper.volumes.v1', 'disk-uuid': str(self.disk_id),
                'volumes': [part.manifest() for part in self.partitions]}

    def vms(self):
        return {'format': 'hyper.vm-config', 'virtual-machines': [
            {'name': vm['name'], 'image': '/data/' + vm['image'],
             'autostart': vm['autostart'],
             'disk': {'client': index, 'volume': vm['name']}}
            for index, vm in enumerate(self.source['virtual-machines'], start=1)]}

    def bootstrap_volumes(self):
        """Strict token projection for the small Linux init helper; JSON owns policy."""
        return 'hyper.volumes.v1\n' + ''.join(
            f'{part.name} {part.identifier} {part.sectors} {part.owner} hyper-{part.name}\n'
            for part in self.partitions)

    def bootstrap_clients(self):
        return 'hyper.clients.v1\n' + ''.join(
            f'{index} {part.name}\n' for index, part in enumerate(self.partitions))
