#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Compose a static bootstrap archive and an independent formal root filesystem."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from board_config import Board, unique_object
from native_libraries import add_dependencies, read_image, validate as validate_libraries
import component_composition

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
        (core if phase == 'boot' else system)['services'].append(service)
    return core, system


def scoped_path(value, selected):
    """Resolve one destination without guessing between boot and formal copies."""
    environment, separator, name = value.partition(':')
    if separator:
        if environment not in ('boot', 'system'):
            raise ValueError(f'invalid deployment environment: {environment}')
        return environment, deployment.archive_path(name)
    name = deployment.archive_path(value)
    matches = [scope for scope, entries in selected.items() if name in entries]
    if len(matches) > 1:
        raise ValueError(f'ambiguous deployment path {name}; use boot:{name} or system:{name}')
    return (matches[0] if matches else None), name


def compose_entries(args, native):
    """Compose independent boot and formal components, including scoped overrides."""
    expected = 'recovery' if args.recovery_only else 'split'
    if native.get('layout') != expected:
        raise ValueError(f'composition requires {expected} layout; embedded system is unsupported')
    roots = {key: str(getattr(args, key)) for key in ('apps', 'sdk', 'std', 'arch')}
    declarations = deployment.load(args.deployment)
    components = {entry['staged']: entry for entry in declarations if 'binary' in entry}
    if len(set(args.boot_components)) != len(args.boot_components):
        raise ValueError('duplicate boot component')
    for name in args.boot_components:
        item = components.get(name)
        if item is None or item.get('environment', 'system') != 'boot':
            raise ValueError(f'unknown boot component: {name}')

    selected = {'boot': {}, 'system': {}}
    boot_executables = set()
    for item in declarations:
        if 'library' in item:
            continue
        environment = item.get('environment', 'system')
        if args.recovery_only and environment == 'system':
            continue
        if environment == 'boot' and 'binary' in item:
            if item['staged'] not in args.boot_components:
                continue
        elif args.profile not in item['profiles']:
            continue
        name = deployment.archive_path(item['destination'].format_map(roots))
        if environment == 'boot' and 'binary' in item:
            boot_executables.add(name)
        if name in selected[environment]:
            raise ValueError(f'duplicate deployment destination: {environment}:{name}')
        if 'symlink' in item:
            record = ['symlink', name, item['symlink'].format_map(roots)]
        else:
            directory = args.boot_apps if environment == 'boot' else args.apps
            source = (str(directory / item['staged']) if 'source' not in item
                      else item['source'].format_map(roots))
            record = [item['mode'], name, source]
        selected[environment][name] = record

    replaced = set()
    for replacement in args.replace:
        path, separator, source = replacement.partition('=')
        if not separator or not source:
            raise ValueError(f'invalid deployment replacement: {replacement}')
        environment, name = scoped_path(path, selected)
        key = (environment, name)
        record = selected.get(environment, {}).get(name)
        if record is None or record[0] == 'symlink':
            raise ValueError(f'replacement not in selected profile: {path}')
        if key in replaced:
            raise ValueError(f'duplicate deployment replacement: {path}')
        replaced.add(key)
        record[2] = source

    archive.validate_entries(args.extra_entries)
    extras = {'boot': [], 'system': []}
    for mode, path, source in triples(args.extra_entries):
        if mode != 'symlink' and not Path(source).is_absolute():
            raise ValueError('additional entry sources must be absolute paths')
        environment, name = scoped_path(path, selected)
        if environment is None:
            environment = 'boot' if mode == 'symlink' or read_image(source) is None else 'system'
        if args.recovery_only and environment != 'boot':
            raise ValueError('stage1 recovery cannot contain formal system entries')
        extras[environment].extend([mode, name, source])
    for environment, entries in extras.items():
        archive.validate_entries(entries)
        selected[environment].update({name: [mode, name, source]
                                      for mode, name, source in triples(entries)})
    for entries in selected.values():
        archive.validate_entries([field for record in entries.values() for field in record])
    boot = list(selected['boot'].values())
    for mode, name, source in boot:
        image = None if mode == 'symlink' else read_image(source)
        if name in boot_executables or image is not None:
            if image is None or image.needed or image.interpreter or image.soname:
                raise ValueError(f'boot component must be a static ELF: {source}')
    boot.append(['symlink', 'lib', 'lib64'])
    if args.recovery_only:
        return boot, [], {}
    system = [field for record in selected['system'].values() for field in record]
    # Resolve the same-build DSO closure for the independent system root. The
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
    return boot, disk, checksums


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
        for name in ('boot', 'tmp', 'data'):
            mountpoint = staging / name
            if mountpoint.is_symlink() or (mountpoint.exists() and not mountpoint.is_dir()):
                raise ValueError(f'system mountpoint must be a directory: /{name}')
            mountpoint.mkdir(exist_ok=True)
            if any(mountpoint.iterdir()):
                raise ValueError(f'system mountpoint must be empty: /{name}')
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
    write_json(manifest_path, core)
    components, startup_policy = component_composition.compose(core, system)
    init_path = metadata / 'init.json'
    write_json(init_path, {'system-root': '/system', 'put-old': '/boot',
                          'system-init': '/svc/component-manager'})
    filesystem_path = metadata / 'system-filesystem.json'
    write_json(filesystem_path, {'worker_directories': [{'environment': 'svc'}]})
    component_config = metadata / 'components.json'
    write_json(component_config, {'configuration_directory': '/etc/components',
                                 'root': 'root', 'startup': '/startup'})
    startup_path = metadata / 'startup.json'
    write_json(startup_path, startup_policy)
    placeholder = metadata / 'empty'
    placeholder.touch()
    boot.extend([
        ['0644', 'etc/hyper/services.json', str(manifest_path.resolve())],
        ['0644', 'etc/hyper/init.json', str(init_path.resolve())],
        ['0644', f'lib64/{args.arch}-hyper-hyper/.keep', str(placeholder.resolve())],
    ])
    boot_fstab = metadata / 'boot-fstab'
    boot_fstab.write_text(native.get('boot-fstab', ''))
    boot = [record for record in boot if record[1] != 'etc/fstab']
    boot.append(['0644', 'etc/fstab', str(boot_fstab.resolve())])
    formal_fstab = metadata / 'fstab'
    formal_fstab.write_text(native.get('fstab', ''))
    disk.append(['0644', 'etc/fstab', str(formal_fstab.resolve())])
    checksums[str(formal_fstab.resolve())] = archive.digest(formal_fstab)
    component_directory = metadata / 'components'
    component_directory.mkdir(exist_ok=True)
    for stale in component_directory.glob('*.json'):
        stale.unlink()
    for name, declaration in sorted(components.items()):
        path = component_directory / f'{name}.json'
        write_json(path, declaration)
        disk.append(['0644', f'etc/components/{name}.json', str(path.resolve())])
        checksums[str(path.resolve())] = archive.digest(path)
    disk.append(['0644', 'etc/hyper/filesystem.json', str(filesystem_path.resolve())])
    checksums[str(filesystem_path.resolve())] = archive.digest(filesystem_path)
    disk.append(['0644', 'etc/hyper/components.json', str(component_config.resolve())])
    checksums[str(component_config.resolve())] = archive.digest(component_config)

    disk.append(['0644', 'etc/hyper/startup.json', str(startup_path.resolve())])
    checksums[str(startup_path.resolve())] = archive.digest(startup_path)

    images = [name for mode, name, source in boot
              if mode != 'symlink' and read_image(source) is not None]
    images.extend(name for mode, name, source in disk
                  if mode != 'symlink' and read_image(source) is not None)
    subprocess.run([sys.executable, str(ROOT / 'scripts/check-service-manifest.py'),
                    str(manifest_path), '--bootstrap', *images], check=True)
    if not args.recovery_only:
        subprocess.run([sys.executable, str(ROOT / 'scripts/check-service-manifest.py'),
                        str(component_directory), '--components', '--startup', str(startup_path), '--core', str(manifest_path), *images], check=True)
    archive.validate_entries([field for record in boot for field in record])
    archive.validate_entries([field for record in disk for field in record])
    # Publish checked staging first. A failed composition must not make an old
    # system disk fingerprint match a newly published bootstrap archive.
    if not args.recovery_only:
        stage_system(disk, args.output / 'system-root', args.strip, checksums)
    archive.validate_entries([field for record in boot for field in record])
    write_json(metadata / 'boot.entries.json', boot)
    subprocess.run([sys.executable, str(ROOT / 'scripts/pack-native-initramfs.py'),
                    '--packer', args.packer, '--strip', args.strip,
                    '--output', str(args.initramfs),
                    '--entries-from', str(metadata / 'boot.entries.json')], check=True)
    if args.recovery_only:
        print(f'Stage1 recovery: {args.initramfs}')
    else:
        print(f'Native system: {args.initramfs} and {args.output}/system-root')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', type=Path)
    parser.add_argument('--board-output', type=Path)
    parser.add_argument('--services', type=Path,
                        help='input service graph; required without --board')
    parser.add_argument('--recovery-only', action='store_true',
                        help='compose static stage1 recovery only, without a formal system payload')
    parser.add_argument('--deployment', type=Path, required=True)
    for name in ('apps', 'sdk', 'std', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--boot-apps', type=Path, help='static boot artifacts; defaults to --apps')
    parser.add_argument('--profile', choices=('system', 'development'), default='system')
    parser.add_argument('--initramfs', type=Path, help='defaults to OUTPUT/bootstrap.cpio')
    parser.add_argument('--replace', action='append', default=[],
                        help='[boot:|system:]PATH=SOURCE; environment required for shared paths')
    parser.add_argument('--entries-from', type=Path, action='append', default=[])
    parser.add_argument('--arch', required=True)
    parser.add_argument('--boot-components', nargs='+', required=True)
    parser.add_argument('--packer', required=True)
    parser.add_argument('--strip', required=True)
    parser.add_argument('extra_entries', nargs='*')
    args = parser.parse_args()
    if len(args.extra_entries) % 3:
        parser.error('extra entries must be MODE ARCHIVE_PATH SOURCE triples')
    if args.board and args.recovery_only:
        parser.error('recovery composition uses --services, not a formal board deployment')
    if args.board and not args.board_output:
        parser.error('--board requires --board-output')
    if not args.board and not args.services:
        parser.error('composition without a board requires --services')
    args.boot_apps = args.boot_apps or args.apps
    for name in ('deployment', 'apps', 'boot_apps', 'sdk', 'std', 'output',
                 'board', 'board_output', 'services', 'initramfs'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    args.initramfs = args.initramfs or args.output / 'bootstrap.cpio'
    try:
        native = {'layout': 'split', 'volumes': [],
                  'boot-fstab': 'PARTLABEL=hyper-system /system auto ro 0 0\n'}
        if args.recovery_only:
            native = {'layout': 'recovery', 'volumes': [], 'boot-fstab': ''}
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
