# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Boot profile composition and archive-local dependency admission.

The bootstrap Rust parser owns runtime policy. These checks run earlier in the
build and resolve its declared files against the payload actually being packed,
not the build directory or the later system filesystem.
"""

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import sys

from native_libraries import read_image, resolve

DIRECTORY = 'etc/hyper/boot-profiles'
MAX_PROFILES = 16
MAX_BYTES = 64 * 1024
MAX_PROFILE_BYTES = 16 * 1024
HEADERS = {'SPDX-FileCopyrightText', 'SPDX-License-Identifier'}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate boot profile key: {key}')
        result[key] = value
    return result


def profile_name(value):
    if not isinstance(value, str) or re.fullmatch(r'[a-zA-Z0-9_-]{1,63}', value) is None:
        raise ValueError('boot profile name must contain 1..63 ASCII letters, digits, underscores or hyphens')
    return value


def absolute_path(value, context):
    if (not isinstance(value, str) or not value.startswith('/') or len(value.encode()) > 4096
            or any(part in ('', '.', '..') for part in value[1:].split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f'{context} must be a canonical absolute path')
    return value


def component_path(value):
    absolute_path(value, 'storage successor')
    if any(re.fullmatch(r'[A-Za-z0-9_.-]{1,63}', part) is None for part in value[1:].split('/')):
        raise ValueError('storage successor must be a component instance path')
    return value


def validate(source):
    if (not isinstance(source, dict)
            or source.keys() - HEADERS - {'version', 'services', 'io', 'handoff', 'requires'}
            or type(source.get('version')) is not int or source['version'] != 1):
        raise ValueError('boot profile requires version 1 and services/io/handoff/requires fields')
    result = {'version': 1, 'services': absolute_path(source.get('services'), 'profile services')}
    io = source.get('io')
    if not isinstance(io, dict) or io.get('kind') not in ('io-vm', 'service', 'none'):
        raise ValueError('boot profile I/O kind must be io-vm, service or none')
    fields = {'kind'} if io['kind'] == 'none' else {'kind', 'service', 'successor'}
    if io.keys() - fields or (io['kind'] != 'none' and 'service' not in io):
        raise ValueError('boot profile I/O source fields do not match its kind')
    result['io'] = dict(io)
    if io['kind'] != 'none':
        profile_name(io['service'])
        if 'successor' in io:
            component_path(io['successor'])
    handoff = source.get('handoff')
    if handoff is not None:
        if not isinstance(handoff, dict) or handoff.keys() != {'system-root', 'put-old', 'system-init'}:
            raise ValueError('profile handoff requires system-root, put-old and system-init')
        result['handoff'] = {key: absolute_path(value, f'handoff {key}') for key, value in handoff.items()}
        if io['kind'] != 'none' and 'successor' not in io:
            raise ValueError('profile storage provider requires a formal successor before handoff')
    elif 'successor' in io:
        raise ValueError('storage successor requires root handoff')
    required = source.get('requires', [])
    if (not isinstance(required, list) or len(required) > 128
            or any(not isinstance(path, str) for path in required)
            or len(set(required)) != len(required)):
        raise ValueError('profile requires must be at most 128 unique archive file paths')
    result['requires'] = sorted(absolute_path(path, 'profile dependency') for path in required)
    if len(json.dumps(result).encode()) > MAX_PROFILE_BYTES:
        raise ValueError('boot profile exceeds 16 KiB')
    return result


def validate_set(source):
    if not isinstance(source, dict) or not 1 <= len(source) <= MAX_PROFILES or 'default' not in source:
        raise ValueError('boot profiles require default and at most 16 named profiles')
    return {profile_name(name): validate(value) for name, value in sorted(source.items())}


def needs_io_vm(product, board=None):
    profiles = product.value.get('boot-profiles') if product is not None else None
    if profiles is None and board is not None and 'io-vm' not in board.source:
        raise ValueError('a board without io-vm requires explicit boot-profiles for its alternate source')
    needed = profiles is None or any(profile['io']['kind'] == 'io-vm' for profile in profiles.values())
    if needed and board is not None and 'io-vm' not in board.source:
        raise ValueError('an io-vm boot profile requires an I/O VM board configuration')
    return needed


def default_profile(core, *, recovery=False, board=None):
    """Describe the generated bootstrap payload, including known early resources."""
    io = next((service for service in core['services']
               if any(grant['purpose'] == 'io.ready' for grant in service.get('capabilities', []))), None)
    if io is None:
        io = next((service for service in core['services'] if service['image'] == '/svc/io-runtime'), None)
    source = {'version': 1, 'services': '/etc/hyper/services.json',
              'io': {'kind': 'none'} if io is None else {'kind': 'io-vm', 'service': io['name']},
              'requires': []}
    if not recovery:
        source['handoff'] = {'system-root': '/system', 'put-old': '/boot',
                             'system-init': '/svc/component-manager'}
        if io is not None:
            source['io']['successor'] = '/io-runtime'
    if any(service['image'] == '/svc/fs-backend' for service in core['services']):
        source['requires'] += ['/etc/fstab', '/etc/hyper/filesystem.json']
        if board is not None and not recovery:
            system = next(part for part in board.source['disk']['partitions'] if part.get('content') == 'system')
            source['requires'].append('/svc/fs-' + system['filesystem'])
    return validate(source)


def compose(product, core, *, recovery=False, board=None):
    profiles = product.value.get('boot-profiles') if product is not None else None
    if board is not None:
        needs_io_vm(product, board)
    if profiles is None:
        return {'default': default_profile(core, recovery=recovery, board=board)}
    return copy.deepcopy(profiles)


def storage_providers(product, profiles):
    configured = product.value['system'].get('storage-providers') if product is not None else None
    if configured is not None:
        return configured
    if any(profile['io']['kind'] == 'io-vm' and 'handoff' in profile for profile in profiles.values()):
        return [{'component': '/io-runtime', 'image': '/svc/io-runtime'}]
    return []


def read_json(path):
    with Path(path).open('rb') as stream:
        contents = stream.read(MAX_BYTES + 1)
    if len(contents) > MAX_BYTES:
        raise ValueError(f'boot profile input exceeds 64 KiB: {path}')
    return json.loads(contents, object_pairs_hook=unique_object)


def validate_archive(records):
    """Check every shipped profile, even one not selected on this boot."""
    entries = {name: (mode, source) for mode, name, source in records}
    paths = sorted(name for name in entries if name.startswith(DIRECTORY + '/'))
    if not paths:
        return {}
    links = {name: source for name, (mode, source) in entries.items() if mode == 'symlink'}

    def file(path, context, *, executable=False):
        name = resolve(absolute_path(path, context)[1:], links)
        record = entries.get(name)
        if record is None or record[0] == 'symlink' or not Path(record[1]).is_file():
            raise ValueError(f'{context}: required file {path} is absent from the boot archive')
        if executable and not int(record[0], 8) & 0o111:
            raise ValueError(f'{context}: service image {path} lacks executable permission')
        return record[1]

    entry = read_image(file('/init', 'bootstrap entry', executable=True)) if 'init' in entries else None
    profiles = {}
    for path in paths:
        name = path.removeprefix(DIRECTORY + '/')
        if not name.endswith('.json') or '/' in name:
            raise ValueError(f'invalid boot profile archive entry: {path}')
        profiles[profile_name(name[:-5])] = validate(read_json(file('/' + path, path)))
    validate_set(profiles)
    for name, profile in profiles.items():
        context = f'boot profile {name}'
        manifest_path = file(profile['services'], context)
        for path in profile['requires']:
            file(path, context)
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict) or not isinstance(manifest.get('services'), list):
            raise ValueError(f'{context}: service manifest must contain a services array')
        boot_images = []
        for service in manifest.get('services', []):
            if not isinstance(service, dict):
                raise ValueError(f'{context}: service declaration must be an object')
            image_path = service.get('image')
            source = file(image_path, context + ' service ' + str(service.get('name', '?')), executable=True)
            image = read_image(source)
            if image is None:
                raise ValueError(f'{context}: service image {image_path} is not an ELF executable')
            if entry is not None and image.machine != entry.machine:
                raise ValueError(f'{context}: service image {image_path} has a different architecture from /init')
            if image.needed or image.interpreter or image.soname:
                raise ValueError(f'{context}: boot service must be a static ELF: {image_path}')
            # Preserve the executable's declared path after resolving and
            # checking its archive-local target; runtime lookup may use a link.
            boot_images.append(image_path.lstrip('/'))
        if profile['io']['kind'] == 'io-vm':
            board = read_json(file('/etc/hyper/board.json', context))
            image = board.get('io-vm', {}).get('image')
            file(image, context + ' I/O VM image')
        subprocess.run([sys.executable, str(Path(__file__).with_name('check-service-manifest.py')),
                        manifest_path, '--bootstrap', '--profile', file('/' + DIRECTORY + '/' + name + '.json', context),
                        *boot_images], check=True)
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('needs-io-vm',))
    parser.add_argument('--system-config', type=Path)
    parser.add_argument('--board', type=Path)
    args = parser.parse_args()
    import system_config
    try:
        product = system_config.load(args.system_config) if args.system_config else None
        from board_config import Board
        board = Board.load(args.board) if args.board else None
        print('yes' if needs_io_vm(product, board) else 'no')
    except (OSError, ValueError) as error:
        parser.exit(1, f'Boot profiles: {error}\n')


if __name__ == '__main__':
    main()
