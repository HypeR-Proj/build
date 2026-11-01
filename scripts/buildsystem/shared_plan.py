# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Resolve the fixed system library set from its owners' manifests and locks."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

from . import files, lock_scope
from .link_context import validate_lock
from .toml import dumps


def merge_locks(locks):
    packages, unused = {}, {}
    for lock in locks:
        for entry in lock.get('package', []):
            key = entry['name'], entry['version'], entry.get('source')
            if key in packages:
                if packages[key].get('checksum') != entry.get('checksum'):
                    raise ValueError(f'conflicting locked checksum for {entry["name"]}')
                dependencies = sorted(set(packages[key].get('dependencies', [])) | set(entry.get('dependencies', [])))
                if dependencies:
                    packages[key]['dependencies'] = dependencies
            else:
                packages[key] = deepcopy(entry)
        for entry in lock.get('patch', {}).get('unused', []):
            unused[json.dumps(entry, sort_keys=True)] = entry
    result = {'version': max(lock['version'] for lock in locks),
              'package': [packages[key] for key in sorted(packages, key=str)]}
    if unused:
        result['patch'] = {'unused': list(unused.values())}
    return result


def prepare(registry, destination, driver, environment, locks, available):
    package = 'hyper-system-libraries'
    data = {'package': {'name': package, 'version': '0.0.0', 'edition': '2024', 'publish': False},
            'workspace': {'resolver': '3'}, 'dependencies': {}}
    for name, (module, entry, manifest) in sorted(registry.items()):
        probe = module.source / entry['link_probe']
        profile = tomllib.loads(probe.read_text()).get('profile', {})
        if 'profile' in data and data['profile'] != profile:
            raise ValueError(f'{name}: shared providers must agree on the compiler profile')
        data['profile'] = profile
        data['dependencies'][name] = {'path': str((module.source / entry['manifest']).parent),
                                      'version': '=' + manifest['package']['version'],
                                      'features': entry['features'], 'default-features': entry['default_features']}
    original = merge_locks(available)
    owned = merge_locks(locks)
    seed = lock_scope.prefer(original, owned)
    body, lock_body = dumps(data).encode(), dumps(seed).encode()
    signature = hashlib.sha256(body + lock_body).hexdigest()
    state = destination / 'providers.json'
    files.write(destination / 'Cargo.toml', body)
    files.write(destination / 'src/main.rs', 'extern crate hyper_rust_std as _;\nfn main() {}\n')
    locked = destination / 'Cargo.lock'
    if not state.is_file() or json.loads(state.read_text()) != {'signature': signature} or not locked.is_file():
        files.write(locked, lock_body)
        subprocess.run([str(driver), 'fetch', '--offline', '--manifest-path', str(destination / 'Cargo.toml')],
                       cwd=destination, env=environment, check=True)
        validate_lock(original, tomllib.loads(locked.read_text()), package)
        files.write(state, json.dumps({'signature': signature}).encode())
    return destination / 'Cargo.toml'
