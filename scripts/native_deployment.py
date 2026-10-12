# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Native deployment policy; disk labels and mount locations are configuration."""

import copy
import re
import uuid
import unicodedata


def partition_source(value):
    if not isinstance(value, str):
        raise ValueError('partition source must be PARTUUID= or PARTLABEL=')
    kind, separator, identifier = value.partition('=')
    if not separator or not identifier:
        raise ValueError('partition source must be PARTUUID= or PARTLABEL=')
    if kind == 'PARTUUID':
        parsed = uuid.UUID(identifier)
        if len(identifier) != 36 or str(parsed) != identifier.lower() or parsed.int == 0:
            raise ValueError('partition UUID must not be nil')
    elif kind == 'PARTLABEL':
        partition_label(identifier)
    else:
        raise ValueError('partition source must be PARTUUID= or PARTLABEL=')
    return value


def partition_label(label):
    if (not isinstance(label, str) or not label or len(label.encode('utf-16-le')) > 72
            or any(unicodedata.category(char) == 'Cc' for char in label)):
        raise ValueError('partition label must contain 1..36 UTF-16 code units without controls')
    return label


def disk_partitions(disk):
    if not isinstance(disk, dict) or disk.keys() != {'uuid', 'partitions'}:
        raise ValueError('disk requires uuid and explicit partitions')
    if uuid.UUID(disk['uuid']).int == 0:
        raise ValueError('disk UUID must not be nil')
    entries = disk['partitions']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ValueError('disk requires 1..128 partitions')
    labels, identifiers, contents = set(), set(), set()
    for entry in entries:
        if (not isinstance(entry, dict) or not {'label', 'size-mib'} <= entry.keys()
                or entry.keys() - {'label', 'size-mib', 'uuid', 'filesystem', 'content', 'image'}):
            raise ValueError('invalid partition fields')
        label = partition_label(entry['label'])
        identifier = uuid.UUID(entry['uuid']) if 'uuid' in entry else uuid.uuid5(uuid.UUID(disk['uuid']), label)
        if label in labels or identifier.int == 0 or identifier in identifiers:
            raise ValueError('duplicate partition label or UUID')
        labels.add(label)
        identifiers.add(identifier)
        size = entry['size-mib']
        if type(size) is not int or not 8 <= size <= 16 * 1024 * 1024:
            raise ValueError('partition size must be 8 MiB..16 TiB')
        filesystem = entry.get('filesystem')
        if filesystem is not None and filesystem not in ('fat', 'erofs'):
            raise ValueError('unsupported partition image format')
        content = entry.get('content')
        if content is not None:
            if content not in ('data', 'system') or content in contents or filesystem is None or 'image' in entry:
                raise ValueError('invalid or duplicate generated partition content')
            contents.add(content)
        if content == 'data' and (filesystem != 'fat' or size < 64):
            raise ValueError('data payload requires a FAT partition of at least 64 MiB')
        if filesystem == 'erofs' and content != 'system' and 'image' not in entry:
            raise ValueError('EROFS partitions require system content or an image')
        if 'image' in entry and (not isinstance(entry['image'], str)
                                or not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', entry['image'])):
            raise ValueError('invalid partition artifact name')
    if 'data' not in contents:
        raise ValueError('one partition must carry the generated data payload')
    return entries


def validate(value):
    if not isinstance(value, dict) or value.keys() - {'layout', 'volumes', 'boot-fstab', 'fstab'}:
        raise ValueError('invalid Native deployment fields')
    if value.get('layout') not in ('split', 'ramfs'):
        raise ValueError('Native layout must be split or ramfs')
    volumes = value.get('volumes', [])
    if not isinstance(volumes, list) or not 1 <= len(volumes) <= 8:
        raise ValueError('invalid Native volume count')
    sources = set()
    for volume in volumes:
        if not isinstance(volume, dict) or volume.keys() != {'source', 'read-only'}:
            raise ValueError('Native volume requires source and read-only')
        source = partition_source(volume['source'])
        if source in sources or type(volume['read-only']) is not bool:
            raise ValueError('duplicate or invalid Native volume')
        sources.add(source)
    for name in ('boot-fstab', 'fstab'):
        table = value.get(name)
        if not isinstance(table, str) or len(table.encode()) > 16384 or '\0' in table:
            raise ValueError(f'Native {name} must be text of at most 16 KiB')
    return {**value, 'volumes': volumes}


def fstab_field(value):
    return value.replace('\\', '\\134').replace(' ', '\\040').replace('\t', '\\011')


def prepare(source, layout=None):
    """Resolve build placement without projecting partition policy into Linux."""
    if layout is not None and layout not in ('split', 'ramfs'):
        raise ValueError('Native layout must be split or ramfs')
    if not isinstance(source, dict):
        raise ValueError('expected a board JSON object')
    result = copy.deepcopy(source)
    partitions = disk_partitions(result.get('disk'))
    if 'native' in result:
        policy = validate(result['native'])
        if layout is not None and policy['layout'] != layout:
            raise ValueError('BOARD_SYSTEM_LAYOUT conflicts with the board Native layout')
    else:
        layout = layout or 'split'
        if layout == 'ramfs':
            partitions = [part for part in partitions if part.get('content') != 'system']
            result['disk']['partitions'] = partitions
        volumes = [{'source': 'PARTLABEL=' + part['label'], 'read-only': part['filesystem'] == 'erofs'}
                   for part in partitions if 'filesystem' in part]
        mounts = {'boot-fstab': [], 'fstab': []}
        for content, target in [('system', '/system'), ('data', '/data')]:
            part = next((part for part in partitions if part.get('content') == content), None)
            if part is not None:
                option = 'ro' if part['filesystem'] == 'erofs' else 'rw'
                table = 'boot-fstab' if content == 'system' else 'fstab'
                mounts[table].append(f"{fstab_field('PARTLABEL=' + part['label'])} {target} auto {option} 0 0\n")
        policy = validate({'layout': layout, 'volumes': volumes,
                           **{name: ''.join(lines) for name, lines in mounts.items()}})
    has_system = any(part.get('content') == 'system' for part in partitions)
    if has_system != (policy['layout'] == 'split'):
        raise ValueError('system partition content must match split/ramfs deployment')
    result['native'] = policy
    return result
