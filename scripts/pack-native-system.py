#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Compose the bootstrap root and formal /system Native environment.

Static core services stay in RAM; system init moves this root to /boot.
The same service graph and paths work with disk-backed or all-RAM /system.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from board_config import Board, unique_object
from native_libraries import add_dependencies, read_image, validate as validate_libraries

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


deployment = module('app_deployment', 'app-deployment.py')
archive = module('pack_native_initramfs', 'pack-native-initramfs.py')


def triples(entries):
    return [entries[index:index + 3] for index in range(0, len(entries), 3)]


def write_json(path, value):
    data = json.dumps(value, indent=2) + '\n'
    if not path.exists() or path.read_text() != data:
        path.write_text(data)


def services(path):
    source = json.loads(path.read_text(), object_pairs_hook=unique_object)
    core = {key: value for key, value in source.items()
            if key not in ('virtual-machines', 'services')}
    system = dict(core)
    core['services'], system['services'] = [], []
    if 'virtual-machines' in source:
        system['virtual-machines'] = source['virtual-machines']
    for service in source['services']:
        loader = service.get('loader', 'dynamic')
        if loader not in ('bootstrap', 'dynamic'):
            raise ValueError(f"unknown loader for service {service['name']}: {loader}")
        phase = 'boot' if loader == 'bootstrap' else 'system'
        if 'phase' in service and service['phase'] != phase:
            raise ValueError(f"service {service['name']} phase conflicts with its loader")
        service['phase'] = phase
        if not service['image'].startswith('/'):
            raise ValueError(f"service {service['name']} image must be absolute")
        if phase == 'system' and not service['image'].startswith('/system/'):
            service['image'] = '/system' + service['image']
        (core if phase == 'boot' else system)['services'].append(service)
    return core, system


def compose_entries(args, native):
    """Partition one selected component set, applying explicit overrides once."""
    roots = {key: str(getattr(args, key)) for key in ('apps', 'sdk', 'std', 'arch')}
    entries = deployment.compose(args.deployment, args.profile, roots, args.replace)
    archive.validate_entries(entries)
    archive.validate_entries(args.extra_entries)
    for mode, _, source in triples(args.extra_entries):
        if mode != 'symlink' and not Path(source).is_absolute():
            raise ValueError('additional entry sources must be absolute paths')
    extras = {name: [mode, name, source] for mode, name, source in triples(args.extra_entries)}
    replacements = {value.partition('=')[0] for value in args.replace}
    selected = {name: [mode, name, source] for mode, name, source in triples(entries)}
    selected.update(extras)

    components = {entry['staged']: entry for entry in deployment.load(args.deployment)
                  if 'binary' in entry}
    boot_images = {}
    if len(set(args.boot_components)) != len(args.boot_components):
        raise ValueError('duplicate boot component')
    for name in args.boot_components:
        if name not in components:
            raise ValueError(f'unknown boot component: {name}')
        item = components[name]
        destination = item['destination']
        if destination in extras or destination in replacements:
            mode, _, source = selected[destination]
        else:
            mode, source = item['mode'], str(args.boot_apps / name)
        image = None if mode == 'symlink' else read_image(source)
        if image is None or image.needed or image.interpreter or image.soname:
            raise ValueError(f'boot component must be a static ELF: {source}')
        boot_images[destination] = [mode, destination, source]

    bootstrap_loader = f'lib64/userspace-loader-hyper-{args.arch}'
    boot = list(boot_images.values())
    system = []
    for name, record in selected.items():
        mode, _, source = record
        if name in boot_images:
            continue
        # Additional configuration and guest payloads are boot data, not
        # applications. Explicit application replacements retain their ELF role.
        boot_data = name in extras and (mode == 'symlink' or read_image(source) is None)
        if name == bootstrap_loader or boot_data:
            boot.append([mode, name, source])
        else:
            system.extend(record)
    # Resolve the same-build DSO closure before placing it under /system. The
    # selected formal loader remains the canonical PT_INTERP identity.
    system = add_dependencies(system, [args.apps / 'lib', args.sdk / 'lib', args.sdk / 'lib64'])
    archive.validate_entries(system)
    checksums = {source: archive.digest(source) for mode, _, source in triples(system)
                 if mode != 'symlink'}
    validate_libraries(system)
    disk = list(triples(system))
    if args.board and any(part.get('content') == 'system' and part.get('filesystem') == 'fat'
                          for part in Board.load(args.board).source['disk']['partitions']):
        disk = [record for record in disk if record != ['symlink', 'lib', 'lib64']]
        if any(mode == 'symlink' for mode, _, _ in disk):
            raise ValueError('FAT system payload cannot represent symbolic links')
    boot.append(['symlink', 'lib', 'lib64'])
    return boot, disk, checksums


def root_switch_policy(native):
    """Userspace chooses the root and mount placement; the kernel knows neither."""
    mounts = [{'source': '/system', 'target': '/system',
               'ramfs': native['layout'] == 'ramfs'}]
    targets = []
    # Only already-mounted bootstrap volumes need moving. Formal mount policy
    # must not constrain handoff: the manager creates those mountpoints later.
    for line in native.get('boot-fstab', '').splitlines():
        # Match fs-backend's token-based comments: '#' inside a label or path
        # is literal, while a whitespace-delimited '#...' starts a comment.
        fields = []
        for field in line.split():
            if field.startswith('#'):
                break
            fields.append(field)
        if len(fields) == 6:
            escapes = {'040': ' ', '011': '\t', '134': '\\'}
            target = re.sub(r'\\(040|011|134)', lambda match: escapes[match[1]], fields[1])
            if target != '/system' and target not in targets:
                targets.append(target)
    mounts.extend({'source': target, 'target': target, 'optional': True}
                  for target in targets)
    return {
        'new-root': '/.newroot', 'put-old': '/boot', 'directories': ['/tmp', '/data'],
        'mounts': mounts,
    }


def stage_system(records, destination, strip, checksums):
    """Publish a complete payload; never leave files from a previous profile."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.system-root-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'payload'
        staging.mkdir()
        for mode, name, source in records:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == 'symlink':
                target.symlink_to(source)
                continue
            shutil.copyfile(source, target)
            os.chmod(target, int(mode, 8))
            if read_image(target) is not None:
                subprocess.run([strip, '--strip-debug', str(target)], check=True)
        if checksums != {source: archive.digest(source) for source in checksums}:
            raise ValueError('system build inputs changed during composition; retry')
        # This is build staging, not a user's mounted or persistent filesystem.
        if destination.is_symlink():
            raise ValueError(f'system staging directory must not be a symlink: {destination}')
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)


def compose(args, native, service_path):
    args.output.mkdir(parents=True, exist_ok=True)
    boot, disk, checksums = compose_entries(args, native)
    metadata = args.output / 'native-system'
    metadata.mkdir(exist_ok=True)
    core, system = services(service_path)
    manifest_path = metadata / 'services.json'
    system_manifest_path = metadata / 'system-services.json'
    write_json(manifest_path, core)
    write_json(system_manifest_path, system)
    init_path = metadata / 'init.json'
    write_json(init_path, {'system-init': '/system/svc/init-system'})
    system_init_path = metadata / 'system-init.json'
    write_json(system_init_path, {'services': '/system/etc/hyper/services.json',
                                 'root-switch': root_switch_policy(native)})
    placeholder = metadata / 'empty'
    placeholder.touch()
    boot.extend([
        ['0644', 'etc/hyper/services.json', str(manifest_path.resolve())],
        ['0644', 'etc/hyper/init.json', str(init_path.resolve())],
        ['0644', f'lib64/{args.arch}-hyper-hyper/.keep', str(placeholder.resolve())],
    ])
    formal_fstab = metadata / 'fstab'
    formal_fstab.write_text(native.get('fstab', ''))
    disk.append(['0644', 'etc/fstab', str(formal_fstab.resolve())])
    checksums[str(formal_fstab.resolve())] = archive.digest(formal_fstab)
    disk.append(['0644', 'etc/hyper/services.json', str(system_manifest_path.resolve())])
    checksums[str(system_manifest_path.resolve())] = archive.digest(system_manifest_path)
    disk.append(['0644', 'etc/hyper/init.json', str(system_init_path.resolve())])
    checksums[str(system_init_path.resolve())] = archive.digest(system_init_path)

    images = [name for mode, name, source in boot
              if mode != 'symlink' and read_image(source) is not None]
    images.extend(name for mode, name, source in disk
                  if mode != 'symlink' and read_image(source) is not None)
    subprocess.run([sys.executable, str(ROOT / 'scripts/check-service-manifest.py'),
                    str(manifest_path), *images], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/check-service-manifest.py'),
                    str(system_manifest_path), '--core', str(manifest_path),
                    *images, *['system/' + name for mode, name, source in disk
                              if mode != 'symlink' and read_image(source) is not None]], check=True)
    archive.validate_entries([field for record in boot for field in record])
    archive.validate_entries([field for record in disk for field in record])
    # Publish checked staging first. A failed composition must not make an old
    # system disk fingerprint match a newly published bootstrap archive.
    stage_system(disk, args.output / 'system-root', args.strip, checksums)
    if native['layout'] == 'ramfs':
        boot.extend([mode, 'system/' + name, source] for mode, name, source in disk)
    archive.validate_entries([field for record in boot for field in record])
    write_json(metadata / 'boot.entries.json', boot)
    subprocess.run([sys.executable, str(ROOT / 'scripts/pack-native-initramfs.py'),
                    '--packer', args.packer, '--strip', args.strip,
                    '--output', str(args.initramfs), '--library-namespace', 'system',
                    '--entries-from', str(metadata / 'boot.entries.json')], check=True)
    print(f'Native system: {args.initramfs} and {args.output}/system-root')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', type=Path)
    parser.add_argument('--board-output', type=Path)
    parser.add_argument('--services', type=Path,
                        help='input service graph; required without --board')
    parser.add_argument('--deployment', type=Path, required=True)
    for name in ('apps', 'sdk', 'std', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--boot-apps', type=Path, help='static boot artifacts; defaults to --apps')
    parser.add_argument('--profile', choices=('system', 'development'), default='system')
    parser.add_argument('--initramfs', type=Path, help='defaults to OUTPUT/bootstrap.cpio')
    parser.add_argument('--replace', action='append', default=[])
    parser.add_argument('--entries-from', type=Path, action='append', default=[])
    parser.add_argument('--arch', required=True)
    parser.add_argument('--boot-components', nargs='+', required=True)
    parser.add_argument('--packer', required=True)
    parser.add_argument('--strip', required=True)
    parser.add_argument('extra_entries', nargs='*')
    args = parser.parse_args()
    if len(args.extra_entries) % 3:
        parser.error('extra entries must be MODE ARCHIVE_PATH SOURCE triples')
    if args.board and not args.board_output:
        parser.error('--board requires --board-output')
    if not args.board and not args.services:
        parser.error('Native ramfs composition requires --services')
    args.boot_apps = args.boot_apps or args.apps
    for name in ('deployment', 'apps', 'boot_apps', 'sdk', 'std', 'output',
                 'board', 'board_output', 'services', 'initramfs'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    args.initramfs = args.initramfs or args.output / 'bootstrap.cpio'
    try:
        native = {'layout': 'ramfs', 'volumes': []}
        service_path = args.services
        if args.board:
            board = Board.load(args.board)
            if board.source['architecture'] != args.arch:
                raise ValueError('board and Native artifact architectures differ')
            native = board.source['native']
            service_path = service_path or args.board_output / 'services.json'
            args.entries_from.insert(0, args.output / 'io.entries.json')
            args.extra_entries.extend(['0644', 'etc/hyper/io-clients.conf',
                                       str(args.board_output / 'io-clients.conf')])
        for path in args.entries_from:
            imported, _ = archive.load_entries(path)
            args.extra_entries.extend(imported)
        compose(args, native, service_path)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Native system: {error}\n')


if __name__ == '__main__':
    main()
