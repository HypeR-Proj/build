# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Read component templates and check their declared Cargo delivery targets."""

import os
from pathlib import Path
import subprocess
import tomllib

from cargo_workspace import member_directories


ROOT = Path(__file__).resolve().parents[1]


def load_registry(path):
    # Use Make itself to expand the templates, rather than interpreting a second
    # subset of Make syntax. Do not inherit a parent's dry-run or jobserver state.
    env = dict(os.environ)
    for name in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES'):
        env.pop(name, None)
    result = subprocess.run(
        ['make', '--no-print-directory', '-s', '-f', str(Path(path).resolve()),
         'component-records'], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    members = (member_directories(ROOT / 'app/Cargo.toml')
               | member_directories(ROOT / 'bootstrap/stage1/Cargo.toml'))
    entries = []
    for record in result.stdout.splitlines():
        fields = record.split('|')
        kind = fields[0]
        if kind in ('binary', 'library') and len(fields) in (6, 7, 8):
            _, source, artifact, staged, destination, profiles = fields[:6]
            runtime = fields[6] if len(fields) >= 7 else 'std'
            if runtime not in ('std', 'core') or (kind == 'library' and runtime != 'std'):
                raise ValueError(f'invalid Rust component runtime: {record}')
            directory = (ROOT / source).resolve()
            if directory not in members:
                raise ValueError(f'component is not a Native workspace member: {source}')
            manifest = tomllib.loads((directory / 'Cargo.toml').read_text())
            static = directory.is_relative_to(ROOT / 'bootstrap/stage1')
            if static and kind == 'library':
                raise ValueError(f'bootstrap/stage1 cannot provide a shared library: {source}')
            if kind == 'binary':
                if artifact not in {item['name'] for item in manifest.get('bin', [])}:
                    raise ValueError(f'component binary is absent from Cargo manifest: {artifact}')
            else:
                library = manifest.get('lib', {})
                name = library.get('name', manifest['package']['name'].replace('-', '_'))
                if 'dylib' not in library.get('crate-type', []) or artifact != f'lib{name}.so':
                    raise ValueError(f'component is not the declared Rust dylib: {artifact}')
            entry = {kind: artifact, 'staged': staged, 'destination': destination,
                     'mode': '0755', 'profiles': profiles.split(),
                     'package': manifest['package']['name'],
                     'link_mode': 'static' if static else 'dynamic',
                     'rust_runtime': runtime, 'environment': 'boot' if static else 'system',
                     'cargo_binaries': [item['name'] for item in manifest.get('bin', [])]}
            if len(fields) == 8 and fields[7]:
                entry['runtime_requires'] = fields[7].split()
        elif kind in ('source', 'symlink') and len(fields) in (5, 6):
            _, source, destination, mode, profiles = fields[:5]
            entry = {kind: source, 'destination': destination, 'mode': mode,
                     'profiles': profiles.split(),
                     'environment': fields[5] if len(fields) == 6 else 'system'}
        else:
            raise ValueError(f'invalid component record: {record}')
        entries.append(entry)
    return {'version': 1, 'entries': entries}


def link_entries(entries, mode):
    """Select one link graph; bootstrap must never unify features with DSOs."""
    return [entry for entry in entries if 'package' in entry
            and (mode is None or entry.get('link_mode', 'dynamic') == mode)]


def extra_binary(entries, name):
    owners = [entry for entry in entries if name in entry.get('cargo_binaries', [])]
    if len(owners) != 1:
        raise ValueError(f'extra binary must have one registered Cargo owner: {name}')
    return owners[0]


def cargo_targets(entries, selected, mode, features, extra_bins, runtime=None, executable_roots=False):
    """Route qualified features and optional probes to their owning link graph."""
    owners = {entry['package']: entry for entry in link_entries(entries, None)}
    roots = [entry for entry in selected if not executable_roots or 'binary' in entry]
    packages = {entry['package'] for entry in roots if 'package' in entry}
    binaries = {entry['binary'] for entry in selected if 'binary' in entry}
    for name in extra_bins:
        owner = extra_binary(entries, name)
        if ((mode is None or owner.get('link_mode', 'dynamic') == mode)
                and (runtime is None or owner.get('rust_runtime', 'std') == runtime)):
            packages.add(owner['package'])
            binaries.add(name)
    selected_features = []
    for feature in features.replace(',', ' ').split():
        package, separator, name = feature.partition('/')
        if not separator or not name or package not in owners:
            raise ValueError(f'feature must name a registered package: {feature}')
        owner = owners[package]
        same_graph = ((mode is None or owner.get('link_mode', 'dynamic') == mode)
                      and (runtime is None or owner.get('rust_runtime', 'std') == runtime))
        if executable_roots and same_graph and package not in packages:
            if 'library' in owner:
                # An explicitly configured DSO feature is an intentional Cargo
                # root, even when ordinary delivery follows executable edges.
                packages.add(package)
                roots.append(owner)
            else:
                raise ValueError(f'feature selects an undeployed application: {feature}')
        if package in packages:
            selected_features.append(feature)
    arguments = [arg for package in sorted(packages) for arg in ('--package', package)]
    arguments += [arg for binary in sorted(binaries) for arg in ('--bin', binary)]
    if any('library' in entry for entry in roots):
        arguments.append('--lib')
    if selected_features:
        arguments += ['--features', ','.join(sorted(set(selected_features)))]
    return arguments
