#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Install and compose applications from their component declarations."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_components import cargo_targets, extra_binary, link_entries, load_registry
from native_libraries import read_image
import system_config


INSTALL_PENDING = '.install-pending.json'


def archive_path(name):
    path = PurePosixPath(name)
    if not name or name == '.' or '\0' in name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError(f'invalid deployment path: {name}')
    return name


def load(path):
    path = Path(path)
    manifest = load_registry(path) if path.suffix == '.mk' else json.loads(path.read_text())
    if manifest['version'] != 1:
        raise ValueError('unsupported application deployment version')
    destinations, artifacts, staged = set(), set(), set()
    entries = manifest['entries']
    for entry in entries:
        name = archive_path(entry['destination'])
        environment = entry.get('environment', 'system')
        if environment not in ('boot', 'system'):
            raise ValueError(f'invalid deployment environment: {environment}')
        key = (environment, name)
        if key in destinations:
            raise ValueError(f'duplicate deployment destination: {environment}:{name}')
        destinations.add(key)
        if not re.fullmatch(r'0[0-7]{3}', entry['mode']):
            raise ValueError(f'invalid deployment mode: {name}')
        if not entry['profiles'] or set(entry['profiles']) - {'system', 'development', 'io'}:
            raise ValueError(f'invalid deployment profiles: {name}')
        if sum(key in entry for key in ('binary', 'library', 'source', 'symlink')) != 1:
            raise ValueError(f'deployment needs exactly one source: {name}')
        if 'symlink' in entry:
            archive_path(entry['symlink'])
            if entry['mode'] != '0777':
                raise ValueError(f'symlink mode must be 0777: {name}')
        if 'binary' in entry or 'library' in entry:
            if entry.get('link_mode', 'dynamic') not in ('static', 'dynamic'):
                raise ValueError(f'invalid component link mode: {name}')
            if entry.get('rust_runtime', 'std') not in ('std', 'core'):
                raise ValueError(f'invalid component Rust runtime: {name}')
            artifact = entry.get('binary', entry.get('library'))
            location = archive_path(entry['staged'])
            pattern = r'[a-zA-Z0-9_-]+' if 'binary' in entry else r'lib[a-zA-Z0-9_-]+\.so'
            if not re.fullmatch(pattern, artifact) or artifact in artifacts or location in staged:
                raise ValueError(f'duplicate or invalid application artifact: {artifact}')
            artifacts.add(artifact); staged.add(location)
    programs = {entry['staged']: entry for entry in entries if 'binary' in entry}
    for entry in entries:
        required = entry.get('runtime_requires', [])
        if (not isinstance(required, list) or any(not isinstance(name, str) for name in required)
                or len(required) != len(set(required))):
            raise ValueError('runtime component dependencies must be unique staged names')
        for name in required:
            if ('binary' not in entry or name not in programs
                    or programs[name].get('environment', 'system') != entry.get('environment', 'system')):
                raise ValueError(f'invalid runtime component dependency: {entry.get("staged")} -> {name}')
    return entries


def compose(manifest, profile, roots, replacements=(), environment='system'):
    entries = []
    overrides = {}
    for replacement in replacements:
        name, separator, source = replacement.partition('=')
        if not separator or not source or name in overrides:
            raise ValueError(f'invalid deployment replacement: {replacement}')
        overrides[name] = source
    for item in load(manifest):
        if profile not in item['profiles'] or item.get('environment', 'system') != environment:
            continue
        # Rust DSOs are providers, not unconditional payload. Packaging follows
        # DT_NEEDED after all executable overrides and fixture entries are known.
        if 'library' in item:
            continue
        name = archive_path(item['destination'].format_map(roots))
        if 'symlink' in item:
            entries.extend(['symlink', name, archive_path(item['symlink'].format_map(roots))])
            continue
        source = (str(Path(roots['apps']) / item['staged']) if 'source' not in item
                  else item['source'].format_map(roots))
        entries.extend([item['mode'], name, overrides.pop(name, source)])
    if overrides:
        raise ValueError(f'replacements not in selected profile: {sorted(overrides)}')
    return entries


def select_components(entries, components):
    """Select executable components by their declared staged names."""
    if not components:
        return entries
    selected = set(components)
    available = {entry['staged'] for entry in entries if 'binary' in entry}
    missing = selected - available
    if missing:
        raise ValueError(f'unknown application components: {sorted(missing)}')
    return [entry for entry in entries
            if 'binary' in entry and entry['staged'] in selected]


def cargo_libraries(entries, records, build):
    """Use Cargo's actual compiled dylibs, never stale files in its output tree."""
    registered = {entry['library']: entry for entry in entries if 'library' in entry}
    found = {}
    build = build.resolve()
    for record in records:
        for line in record.read_text().splitlines():
            message = json.loads(line)
            target = message.get('target', {})
            if (message.get('reason') != 'compiler-artifact'
                    or 'dylib' not in target.get('crate_types', [])):
                continue
            name = f"lib{target['name']}.so"
            if name not in registered:
                raise ValueError(f'unregistered Native shared library: {name}')
            paths = [Path(path).resolve() for path in message['filenames'] if Path(path).name == name]
            if len(paths) != 1 or paths[0].parent not in (build, build / 'deps'):
                raise ValueError(f'Cargo shared library is outside the selected build graph: {name}')
            if name in found and found[name][1] != paths[0]:
                raise ValueError(f'ambiguous Cargo shared library: {name}')
            found[name] = (registered[name], paths[0])
    return list(found.values())


def managed_path(output, name):
    path = output / archive_path(name)
    if name in (INSTALL_PENDING, 'build-manifest.json'):
        raise ValueError(f'reserved staged artifact path: {name}')
    if not path.parent.resolve().is_relative_to(output.resolve()):
        raise ValueError(f'staged artifact escapes output: {name}')
    return path


def write_record(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    if not path.is_file() or path.read_bytes() != data:
        with tempfile.TemporaryDirectory(prefix='.record-', dir=path.parent) as directory:
            temporary = Path(directory) / path.name
            temporary.write_bytes(data)
            temporary.replace(path)


def require_complete_install(output):
    if (output / INSTALL_PENDING).exists():
        raise ValueError(f'{output}: application installation is incomplete; rerun the application build')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['binaries', 'cargo-targets', 'install', 'manifest', 'host-excludes',
                                          'config-id', 'boot-components', 'kernel-features'])
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--build', type=Path)
    parser.add_argument('--static-build', type=Path,
                        help='bootstrap build tree when installing a mixed application set')
    parser.add_argument('--core-build', type=Path,
                        help='no_std target output when installing a mixed runtime set')
    parser.add_argument('--static-core-build', type=Path,
                        help='static no_std target output when installing a mixed runtime set')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--extra-bin', action='append', default=[])
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--features', default='')
    parser.add_argument('--system-config', type=Path, help='product component and kernel feature selection')
    parser.add_argument('--cargo-artifacts', type=Path, action='append', default=[],
                        help='Cargo JSON messages for the completed build graphs')
    parser.add_argument('--executable-roots', action='store_true',
                        help='let Cargo compile only libraries required by selected programs')
    parser.add_argument('--profile', choices=['system', 'development', 'io'],
                        help='build and install only components in this deployment profile')
    parser.add_argument('--component', action='append', default=[],
                        help='select an executable by its staged component name')
    parser.add_argument('--link-mode', choices=['dynamic', 'static'],
                        help='select only components belonging to this link graph')
    parser.add_argument('--rust-runtime', choices=['std', 'core'],
                        help='select a Cargo runtime graph without std feature unification')
    args = parser.parse_args()
    all_entries = load(args.manifest)
    product = system_config.load(args.system_config) if args.system_config else None
    product_entries = product.select(all_entries, args.profile or 'system') if product else None
    if args.action == 'config-id':
        print(product.identity() if product else '')
        return
    if args.action == 'kernel-features':
        kernel = product.value.get('kernel') if product else None
        print(','.join(kernel['features']) if kernel is not None else 'default')
        return
    if args.action == 'boot-components':
        selection = product or system_config.ProductConfig({'version': 1})
        print(' '.join(selection.boot_components(all_entries)))
        return
    entries = product_entries if product is not None else all_entries
    entries = select_components(entries, args.component)
    if args.profile and product is None:
        filtered = [entry for entry in entries if args.profile in entry['profiles']]
        if args.component and len(filtered) != len(entries):
            parser.error('component selection conflicts with its deployment profile')
        entries = filtered
    if args.link_mode:
        filtered = link_entries(entries, args.link_mode)
        if args.component and len(filtered) != len(entries):
            parser.error('component selection conflicts with its required link mode')
        entries = filtered
    if args.rust_runtime:
        entries = [entry for entry in entries
                   if entry.get('rust_runtime', 'std') == args.rust_runtime]
    if args.action == 'manifest':
        print(json.dumps({'version': 1, 'entries': entries}, indent=2))
        return
    if args.action == 'host-excludes':
        print(' '.join('--exclude ' + entry['package'] for entry in entries if 'library' in entry))
        return
    if args.action == 'binaries':
        print(' '.join('--bin ' + entry['binary'] for entry in entries if 'binary' in entry))
        return
    if args.action == 'cargo-targets':
        print(' '.join(cargo_targets(all_entries, entries, args.link_mode,
                                     args.features, args.extra_bin, args.rust_runtime,
                                     args.executable_roots)))
        return
    if args.build is None or args.output is None:
        parser.error('install requires --build and --output')
    artifacts = []
    for entry in entries:
        if 'binary' in entry or ('library' in entry and not args.cargo_artifacts):
            artifacts.append((entry, entry.get('binary', entry.get('library')), entry['staged']))
    for binary in args.extra_bin:
        if not re.fullmatch(r'hyper-[a-zA-Z0-9_-]+', binary):
            parser.error('extra binaries must have a hyper- prefixed Cargo target name')
        entry = extra_binary(all_entries, binary)
        if ((args.link_mode is None or entry.get('link_mode', 'dynamic') == args.link_mode)
                and (args.rust_runtime is None or entry.get('rust_runtime', 'std') == args.rust_runtime)):
            artifacts.append((entry, binary, binary.removeprefix('hyper-')))
    sources = []
    if args.cargo_artifacts:
        sources.extend((source, entry['staged'], entry['mode'])
                       for entry, source in cargo_libraries(all_entries, args.cargo_artifacts, args.build))
    # Check every bootstrap artifact before replacing any staged image. The
    # restriction follows source ownership, independently of installed paths.
    for entry, binary, staged in artifacts:
        static = entry.get('link_mode', 'dynamic') == 'static'
        build = (args.static_build or args.build) if static else args.build
        if entry.get('rust_runtime', 'std') == 'core':
            build = (args.static_core_build or args.core_build or build) if static else (args.core_build or build)
        source = build / binary
        if static:
            image = read_image(source)
            if image is None or image.interpreter or image.needed or image.soname:
                parser.error(f'{source}: bootstrap/stage1 requires a static ELF')
            if args.sdk:
                subprocess.run([str(args.sdk / 'bin/hyper-brand-elf'), '--check-static',
                                str(source)], check=True)
        sources.append((source, staged, entry['mode']))
    # Validate the whole source set before touching staged images. Track every
    # possibly changed path before copying, so a failed install can be retried
    # with a smaller selection without leaving unrecorded library providers.
    current = {staged for _, staged, _ in sources}
    for source, staged, _ in sources:
        if not source.is_file():
            raise ValueError(f'application artifact is missing: {source}')
        destination = managed_path(args.output, staged)
        if destination.is_symlink():
            raise ValueError(f'staged artifact must not be a symlink: {destination}')
    manifest_path = args.output / 'build-manifest.json'
    previous = set()
    if manifest_path.is_file():
        artifacts = json.loads(manifest_path.read_text()).get('artifacts', {})
        if not isinstance(artifacts, dict):
            raise ValueError('invalid installed artifact manifest')
        previous.update(artifacts)
    pending = args.output / INSTALL_PENDING
    if pending.is_file():
        interrupted = json.loads(pending.read_text())
        if (not isinstance(interrupted, dict) or interrupted.get('version') != 1
                or not isinstance(interrupted.get('artifacts'), list)
                or any(not isinstance(name, str) for name in interrupted['artifacts'])):
            raise ValueError('invalid pending application installation')
        previous.update(interrupted['artifacts'])
    for name in previous:
        managed_path(args.output, name)
    args.output.mkdir(parents=True, exist_ok=True)
    write_record(pending, {'version': 1, 'artifacts': sorted(previous | current)})
    installer = Path(__file__).with_name('install-if-changed.sh')
    installed = []
    for source, staged, mode in sources:
        destination = args.output / staged
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['sh', str(installer), mode, str(source), str(destination)], check=True)
        installed.append(destination)
    for stale in previous - current:
        path = managed_path(args.output, stale)
        if path.is_file() or path.is_symlink():
            path.unlink()
    # Record the installed set only after every copy succeeds. This is
    # provenance for the exact staged files, not a separate dependency graph.
    record = {
        'source_revision': subprocess.check_output(
            ['git', 'describe', '--always', '--dirty'], cwd=Path(__file__).resolve().parents[1],
            text=True).strip(),
        'rustc': subprocess.check_output(['rustc', '-vV'], text=True),
        'features': sorted(set(args.features.replace(',', ' ').split())),
        'link_mode': args.link_mode or 'mixed',
        'components': sorted(set(args.component)),
        'profile': args.profile,
        'extra_binaries': sorted(set(args.extra_bin)),
        'sdk': {name: (args.sdk / 'share/hyper' / name).read_text().strip()
                for name in ('manifest', 'link-fingerprint', 'std-fingerprint')} if args.sdk else {},
        'system_config': product.value if product else None,
        'system_config_id': product.identity() if product else None,
        'cargo_artifacts': [str(path) for path in args.cargo_artifacts],
        'artifacts': {str(path.relative_to(args.output)): {
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}
            for path in sorted(installed)},
    }
    write_record(manifest_path, record)
    pending.unlink()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Native deployment: {error}')
