# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Normalize Cargo's LTO context without changing repository dependency locks.

Cargo distinguishes a static dependency used only inside a dylib from one also
reachable by an executable. A compile-only rlib/dylib pair makes both forms of
reachability consistent, including nested shared providers. The helper must
never appear in a delivered image's DT_NEEDED. Source locks remain authoritative.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
from urllib.parse import parse_qsl, unquote, urlsplit

from . import files, lock_scope
from .toml import dumps


def link_units(graph):
    units = graph['units']
    pending = [index for index, unit in enumerate(units) if 'dylib' in unit['target']['crate_types']]
    visited = set()
    result = {units[index]['pkg_id']: units[index] for index in pending}
    while pending:
        index = pending.pop()
        if index in visited:
            continue
        visited.add(index)
        for edge in units[index]['dependencies']:
            child = units[edge['index']]
            if (edge['noprelude'] or not child['platform'] or child['mode'] != 'build'
                    or not {'lib', 'rlib', 'dylib'} & set(child['target']['crate_types'])):
                continue
            pending.append(edge['index'])
            result[child['pkg_id']] = child
    return result


def dependency(identity, unit, lock):
    source, package = identity.rsplit('#', 1)
    if '@' in package:
        name, version = package.rsplit('@', 1)
    else:
        name, version = Path(unquote(urlsplit(source[5:]).path)).name, package
    result = {'package': name, 'version': '=' + version,
              'features': unit['features'], 'default-features': False}
    if source.startswith('path+'):
        result['path'] = unquote(urlsplit(source[5:]).path)
    elif source.startswith('git+'):
        matches = [entry['source'] for entry in lock['package'] if entry['name'] == name
                   and entry['version'] == version and entry.get('source', '').startswith('git+')]
        if len(matches) != 1:
            raise ValueError(f'ambiguous locked shared dependency: {identity}')
        url = urlsplit(matches[0][4:])
        result['git'] = url._replace(query='', fragment='').geturl()
        result.update({key: value for key, value in parse_qsl(url.query) if key in ('branch', 'tag', 'rev')})
    elif source != 'registry+https://github.com/rust-lang/crates.io-index':
        raise ValueError(f'unsupported shared dependency registry: {source}')
    return result


def validate_lock(original, generated, anchor):
    allowed = {(entry['name'], entry['version'], entry.get('source')): entry.get('checksum')
               for entry in original['package']}
    for entry in generated['package']:
        key = entry['name'], entry['version'], entry.get('source')
        if key == (anchor, '0.0.0', None):
            continue
        if key not in allowed or allowed[key] != entry.get('checksum'):
            raise ValueError(f'link context changed a repository-locked dependency: {entry["name"]}')


def project(manifest, workspace):
    data = tomllib.loads(manifest.read_text())
    root = tomllib.loads(workspace.read_text())
    data['package'].pop('workspace', None)
    data['workspace'] = {'resolver': root.get('workspace', {}).get('resolver', '3')}
    data['profile'] = deepcopy(root.get('profile', {}))
    for key in ('patch', 'replace'):
        if key in root:
            data[key] = deepcopy(root[key])
            scopes = data[key].values() if key == 'patch' else [data[key]]
            for scope in scopes:
                for declaration in scope.values():
                    if isinstance(declaration, dict) and 'path' in declaration:
                        declaration['path'] = str((workspace.parent / declaration['path']).resolve())
    for scope in [data, *data.get('target', {}).values()]:
        for section in ('dependencies', 'dev-dependencies', 'build-dependencies'):
            for declaration in scope.get(section, {}).values():
                if isinstance(declaration, dict) and 'path' in declaration:
                    declaration['path'] = str((manifest.parent / declaration['path']).resolve())
    if (manifest.parent / 'src/lib.rs').exists() or 'lib' in data:
        library = data.setdefault('lib', {})
        library['path'] = str(manifest.parent / library.get('path', 'src/lib.rs'))
    if not data.get('bin') and (manifest.parent / 'src/main.rs').exists():
        data['bin'] = [{'name': data['package']['name'], 'path': 'src/main.rs'}]
    for binary in data.get('bin', []):
        binary['path'] = str(manifest.parent / binary.get('path', 'src/main.rs'))
    build = data['package'].get('build', 'build.rs')
    if isinstance(build, str) and (manifest.parent / build).is_file():
        data['package']['build'] = str(manifest.parent / build)
    return data


def source_context(manifest, workspace, destination, controls):
    """Copy a source manifest and lock into an independently resolved output view."""
    body = dumps(project(manifest, workspace)).encode()
    lock_body = (workspace.parent / 'Cargo.lock').read_bytes()
    signature = hashlib.sha256(body + lock_body + controls.encode()).hexdigest()
    state = destination / 'source-context.json'
    destination.mkdir(parents=True, exist_ok=True)
    for source in manifest.parent.iterdir():
        if source.name not in ('Cargo.toml', 'Cargo.lock', 'target', '.git', '.cargo'):
            files.link(destination / source.name, source)
    files.write(destination / 'Cargo.toml', body)
    locked = destination / 'Cargo.lock'
    if not state.is_file() or json.loads(state.read_text()) != {'signature': signature} or not locked.is_file():
        files.write(locked, lock_body)
        files.write(state, json.dumps({'signature': signature}).encode())
    return destination / 'Cargo.toml'


def preview(manifest, workspace, destination, driver, environment, controls, resolve=None):
    """Resolve an output-only view; callers validate the owned dependency closure."""
    context = source_context(manifest, workspace, destination, controls)
    command = [str(driver), 'fetch', '--manifest-path', str(context)]
    if resolve is None:
        subprocess.run(command, cwd=destination, env=environment, check=True)
    else:
        resolve(command)
    return context


def prepare(manifest, workspace, graph, destination, driver, environment, contract=None, contract_lock=None):
    private = link_units(contract or graph)
    if not private:
        return manifest
    data = project(manifest, workspace)
    lock_body = (workspace.parent / 'Cargo.lock').read_bytes()
    original_lock = tomllib.loads(lock_body.decode())
    if contract_lock is not None:
        from .shared_plan import merge_locks
        original_lock = merge_locks([original_lock, contract_lock])
        lock_body = dumps(lock_scope.prefer(original_lock, contract_lock)).encode()
    anchor_name = 'hyper-link-context-' + data['package']['name']
    anchor = {'package': {'name': anchor_name, 'version': '0.0.0', 'edition': '2024', 'publish': False},
              'lib': {'crate-type': ['dylib', 'rlib']}, 'dependencies': {}}
    direct = {graph['units'][edge['index']]['pkg_id'] for index in graph['roots']
              for edge in graph['units'][index]['dependencies']}
    standard = None
    for identity, unit in sorted(private.items()):
        alias = 'dependency_' + hashlib.sha256(identity.encode()).hexdigest()[:16]
        anchor['dependencies'][alias] = dependency(identity, unit, original_lock)
        if unit['target']['name'] == 'hyper_rust_std':
            standard = alias
        if 'dylib' in unit['target']['crate_types'] and identity not in direct:
            data.setdefault('dependencies', {})['__hyper_provider_' + alias] = deepcopy(anchor['dependencies'][alias])
    if standard is None:
        raise ValueError('Native shared providers must consume the system Rust standard library')
    data.setdefault('dependencies', {})['__hyper_link_context'] = {'package': anchor_name, 'path': 'link-context'}
    body, anchor_body = dumps(data).encode(), dumps(anchor).encode()
    signature = hashlib.sha256(body + anchor_body + lock_body).hexdigest()
    state = destination / 'link-context.json'
    destination.mkdir(parents=True, exist_ok=True)
    for source in manifest.parent.iterdir():
        if source.name not in ('Cargo.toml', 'Cargo.lock', 'target', '.git', '.cargo', 'link-context', 'link-context.json'):
            files.link(destination / source.name, source)
    files.write(destination / 'Cargo.toml', body)
    files.write(destination / 'link-context/Cargo.toml', anchor_body)
    files.write(destination / 'link-context/src/lib.rs',
                f'extern crate {standard} as _;\n'
                '#[unsafe(no_mangle)]\npub extern "C" fn __hyper_link_context_marker() {}\n')
    locked = destination / 'Cargo.lock'
    if not state.is_file() or json.loads(state.read_text()) != {'signature': signature} or not locked.is_file():
        files.write(locked, lock_body)
        subprocess.run([str(driver), 'fetch', '--offline', '--manifest-path', str(destination / 'Cargo.toml')],
                       cwd=destination, env=environment, check=True)
        validate_lock(original_lock, tomllib.loads(locked.read_text()), anchor_name)
        files.write(state, json.dumps({'signature': signature}).encode())
    return destination / 'Cargo.toml'
