# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Assign dependency locks to the executable or shared provider that owns them."""

from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit


def package(identity):
    source, value = identity.rsplit('#', 1)
    if '@' in value:
        name, version = value.rsplit('@', 1)
    else:
        name, version = Path(unquote(urlsplit(source[5:]).path)).name, value
    return name, version, None if source.startswith('path+') else source


def owned(graph, boundaries, owner=None, ignored=()):
    """Walk caller edges, stopping at foreign shared-library boundaries.

    A private dependency also reached directly from the caller is still owned
    by that caller. Compiler-injected sysroot edges have a separate SDK owner.
    """
    units, pending, visited, result = graph['units'], list(graph['roots']), set(), set()
    roots = {units[index]['pkg_id'] for index in pending}
    while pending:
        index = pending.pop()
        if index in visited:
            continue
        visited.add(index)
        unit = units[index]
        if unit.get('is_std'):
            continue
        identity = unit['pkg_id']
        name = package(identity)[0]
        if identity in ignored or (name in boundaries and name != owner):
            continue
        if identity not in roots:
            result.add(package(identity))
        pending.extend(edge['index'] for edge in unit['dependencies'] if not edge['noprelude'])
    return result


def select(lock, packages):
    def matches(entry):
        source = entry.get('source')
        # Unit IDs identify the Git dependency; Cargo.lock additionally records
        # its resolved commit. Preserve the full locked source and checksum.
        if source and source.startswith('git+'):
            source = source.split('#', 1)[0]
        return (entry['name'], entry['version'], source) in packages
    result = [entry for entry in lock['package'] if matches(entry)]
    if len(result) != len(packages):
        matched = {(entry['name'], entry['version'], entry.get('source', '').split('#', 1)[0] or None) for entry in result}
        raise ValueError('compiled dependency closure does not match its resolved Cargo.lock: '
                         + ', '.join(map(str, sorted(packages - matched, key=str))))
    return {'version': lock['version'], 'package': result}


def validate(original, resolved, packages):
    allowed = {(entry['name'], entry['version'], entry.get('source')): entry.get('checksum')
               for entry in original['package']}
    selected = select(resolved, packages)
    for entry in selected['package']:
        key = entry['name'], entry['version'], entry.get('source')
        if key not in allowed or allowed[key] != entry.get('checksum'):
            raise ValueError(f'changed a repository-owned locked dependency: {entry["name"]}')
    return selected


def prefer(available, selected):
    """Seed Cargo with owner-selected versions instead of stale transitive copies.

    Retain unrelated/platform-only records for Cargo's all-target resolution.
    Ownership validation must still reject changes to a caller's direct closure.
    """
    names = {entry['name'] for entry in selected['package']}
    keys = {(entry['name'], entry['version'], entry.get('source')) for entry in selected['package']}
    result = deepcopy(available)
    result['package'] = [entry for entry in result['package'] if entry['name'] not in names
                         or (entry['name'], entry['version'], entry.get('source')) in keys]
    return result
