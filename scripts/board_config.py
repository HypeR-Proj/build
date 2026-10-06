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
# Ordinary FAT data volume; neither direct boot profile uses UEFI.
BASIC_DATA = uuid.UUID('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7')
# Opaque VM disks must not be advertised as host Linux filesystems or LVM PVs.
VM_DISK = uuid.UUID('a6edb737-452f-4a43-bd9f-bc04aa5323cf')
_NAME = re.compile(r'[a-z][a-z0-9-]{0,30}\Z')
_VM_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z')
_INTERFACE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,14}\Z')
_MAC = re.compile(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\Z')
_LICENSE = {'SPDX-FileCopyrightText', 'SPDX-License-Identifier'}


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError('expected a JSON object')
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional) - _LICENSE
    if missing or extra:
        raise ValueError(f'invalid configuration keys: missing={sorted(missing)}, unknown={sorted(extra)}')


def vm_configuration(value):
    keys(value, ('memory-bytes', 'vcpus', 'bootargs'), ('affinity',))
    integer(value['vcpus'], 1, 8)
    memory = integer(value['memory-bytes'], 64 * MIB, 1 << 63)
    if memory & (memory - 1):
        raise ValueError('VM memory-bytes must be a power of two')
    args = value['bootargs']
    if not isinstance(args, str) or len(args.encode()) > 2048 or '\0' in args:
        raise ValueError('VM bootargs must contain at most 2048 bytes and no NUL')
    affinity = value.get('affinity', [])
    if not isinstance(affinity, list) or len(affinity) > value['vcpus']:
        raise ValueError('VM affinity must be a list with at most one entry per vCPU')
    seen = set()
    for entry in affinity:
        keys(entry, ('vcpu', 'cpus'))
        vcpu = integer(entry['vcpu'], 0, value['vcpus'] - 1)
        if vcpu in seen:
            raise ValueError('VM affinity contains a duplicate vCPU')
        seen.add(vcpu)
        cpus = entry['cpus']
        # Native ABI: four 64-bit host CPU mask words.
        if not isinstance(cpus, list) or not 1 <= len(cpus) <= 256:
            raise ValueError('VM affinity requires a nonempty bounded CPU list')
        for cpu in cpus:
            integer(cpu, 0, 255)
        if len(set(cpus)) != len(cpus):
            raise ValueError('VM affinity contains a duplicate host CPU')
    return value


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


def device_selector(selector, profiles):
    keys(selector, ('profile',), ('compatible', 'path', 'pci-id'))
    if selector['profile'] not in profiles:
        raise ValueError('unsupported assignment profile')
    identities = [key for key in ('compatible', 'path', 'pci-id') if key in selector]
    if len(identities) != 1:
        raise ValueError('device requires exactly one firmware identity')
    identity = selector[identities[0]]
    if (selector['profile'] == 'pci-function') != (identities[0] == 'pci-id'):
        raise ValueError('device identity does not match its assignment profile')
    if identities[0] == 'pci-id' and (not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{4}', identity)):
        raise ValueError('PCI identity must be canonical vvvv:dddd hexadecimal')
    if not isinstance(identity, str) or not identity or len(identity.encode()) > 512 or any(ord(c) <= 32 or ord(c) == 127 for c in identity):
        raise ValueError('invalid firmware identity')
    if identities[0] == 'path' and (not identity.startswith('/') or any(part in ('', '.', '..') for part in identity.split('/')[1:])):
        raise ValueError('firmware path must be canonical and absolute')


def io_networks(value):
    if ('network-device' in value) != ('networks' in value):
        raise ValueError('network-device and networks must be configured together')
    if 'network-device' not in value:
        return
    device_selector(value['network-device'], ('virtio-mmio-net', 'pci-function'))
    networks = value['networks']
    if not isinstance(networks, list) or len(networks) != 1:
        raise ValueError('network deployment requires exactly one uplink and bridge')
    network = networks[0]
    keys(network, ('name', 'bridge', 'uplink'))
    name(network['name'])
    for field in ('bridge', 'uplink'):
        if not isinstance(network[field], str) or not _INTERFACE.fullmatch(network[field]):
            raise ValueError('invalid Linux network interface name')
    if network['bridge'] == network['uplink']:
        raise ValueError('network bridge and uplink must be different interfaces')


def vm_network(value, networks):
    keys(value, ('network', 'mac'))
    if name(value['network']) not in networks:
        raise ValueError('VM network must name a configured I/O VM network')
    mac = value['mac']
    if not isinstance(mac, str) or not _MAC.fullmatch(mac) or int(mac[:2], 16) & 3 != 2:
        raise ValueError('VM MAC must be a canonical locally administered unicast address')
    return mac


def io_vm(value):
    keys(value, ('runtime', 'name', 'image', 'configuration', 'io-device'),
         ('network-device', 'networks'))
    if value['runtime'] != 'io-runtime':
        raise ValueError('resident I/O VM requires io-runtime')
    if not isinstance(value['name'], str) or not _VM_NAME.fullmatch(value['name']):
        raise ValueError('invalid I/O VM name')
    image = value['image']
    if (not isinstance(image, str) or not image.startswith('/vm/') or not image.endswith('.itb')
            or len(image.encode()) > 512 or '\\' in image
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in image)
            or any(part in ('', '.', '..') for part in image.split('/')[1:])):
        raise ValueError('I/O VM image must be a canonical /vm/ ITB path of at most 512 bytes')
    config = vm_configuration(value['configuration'])
    if config['memory-bytes'] != 128 * MIB or config['vcpus'] != 1:
        raise ValueError('resident I/O VM requires 128 MiB and one vCPU')
    device_selector(value['io-device'], ('virtio-mmio-scsi', 'bcm2712-sdhci'))
    io_networks(value)
    return value


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
        keys(data, ('format', 'board', 'architecture', 'boot', 'disk', 'files', 'virtual-machines', 'io-vm'))
        if data['format'] != 'hyper.board.v1' or data['architecture'] != 'aarch64':
            raise ValueError('unsupported board format or architecture')
        name(data['board'])
        resident = io_vm(data['io-vm'])
        if data['boot'] not in ('qemu-direct', 'rpi5-firmware'):
            raise ValueError('unsupported boot chain')
        if 'network-device' in resident:
            expected = {'qemu-direct': 'virtio-mmio-net',
                        'rpi5-firmware': 'pci-function'}[data['boot']]
            if resident['network-device']['profile'] != expected:
                raise ValueError('network controller does not match the board boot profile')
        keys(data['disk'], ('uuid', 'config-mib'))
        disk_id = uuid.UUID(data['disk']['uuid'])
        if disk_id.int == 0:
            raise ValueError('disk UUID must not be nil')
        config_size = integer(data['disk']['config-mib'], 64, 32768) * MIB // SECTOR
        partitions = [Partition('config', ALIGN_SECTORS, config_size,
                                uuid.uuid5(disk_id, 'config'), BASIC_DATA, 'hyper')]
        if not isinstance(data['files'], dict):
            raise ValueError('files must map destination paths to artifact names')
        required_files = {'hyper.img'} if data['boot'] == 'rpi5-firmware' else set()
        if data['boot'] == 'rpi5-firmware':
            required_files |= {'bootstrap.cpio', 'bcm2712-rpi-5-b.dtb',
                               'bcm2712d0-rpi-5-b.dtb',
                               'overlays/bcm2712d0.dtbo', 'overlays/overlay_map.dtb'}
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
        seen = {'config', resident['name']}
        networks = {network['name'] for network in resident.get('networks', [])}
        macs = set()
        for vm in vms:
            keys(vm, ('name', 'image', 'autostart', 'configuration'),
                 ('disk-mib', 'disk-image', 'network'))
            vm_configuration(vm['configuration'])
            vm_name = name(vm['name'])
            if vm_name in seen:
                raise ValueError('duplicate or reserved VM name')
            seen.add(vm_name)
            image = relative_path(vm['image'])
            if image not in data['files'] or not image.endswith('.itb'):
                raise ValueError('VM image must name an explicitly packaged ITB')
            if type(vm['autostart']) is not bool:
                raise ValueError('autostart must be boolean')
            if 'network' in vm:
                mac = vm_network(vm['network'], networks)
                if mac in macs:
                    raise ValueError('VM MAC addresses must be unique')
                macs.add(mac)
            if 'disk-mib' not in vm:
                if 'disk-image' in vm:
                    raise ValueError('disk-image requires disk-mib')
                continue
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
        definitions = []
        for client, vm in enumerate(self.source['virtual-machines'], start=1):
            definition = {'name': vm['name'], 'image': '/data/' + vm['image'],
                          'autostart': vm['autostart'], 'configuration': vm['configuration']}
            if 'disk-mib' in vm:
                definition['disk'] = {'client': client, 'volume': vm['name']}
            if 'network' in vm:
                definition['network'] = {'client': client, **vm['network']}
            definitions.append(definition)
        return {'format': 'hyper.vm-config', 'virtual-machines': definitions}

    def bootstrap_volumes(self):
        """Strict token projection for the small Linux init helper; JSON owns policy."""
        return 'hyper.volumes.v1\n' + ''.join(
            f'{part.name} {part.identifier} {part.sectors} {part.owner} hyper-{part.name}\n'
            for part in self.partitions)

    def bootstrap_clients(self):
        rows = ['hyper.clients.v2', '0 config - -']
        for client, vm in enumerate(self.source['virtual-machines'], start=1):
            volume = vm['name'] if 'disk-mib' in vm else '-'
            network = vm.get('network')
            if volume == '-' and network is None:
                continue
            rows.append(f"{client} {volume} {network['network'] if network else '-'} "
                        f"{network['mac'] if network else '-'}")
        return '\n'.join(rows) + '\n'

    def bootstrap_networks(self):
        return 'hyper.networks.v1\n' + ''.join(
            f"{network['name']} {network['bridge']} {network['uplink']}\n"
            for network in self.source['io-vm'].get('networks', []))
