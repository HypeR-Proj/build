# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""One declared Native Rust provider per library, with checked compile identity."""

from copy import deepcopy
import hashlib
import json
import tomllib


def providers(modules):
    result = {}
    for module in modules.values():
        for entry in module.metadata.get('cargo', []):
            if not entry.get('shared'):
                continue
            manifest = module.source / entry['manifest']
            data = tomllib.loads(manifest.read_text())
            name = data['package']['name']
            if name in result or data.get('lib', {}).get('crate-type') != ['dylib']:
                raise ValueError(f'{module.name}: shared provider must identify one unique dylib package')
            if entry.get('domain') != 'system' or not entry.get('link_probe'):
                raise ValueError(f'{module.name}: shared providers require a system link_probe')
            result[name] = (module, entry, data)
    return result


def feature_closure(data, entry):
    definitions = data.get('features', {})
    optional = {name for name, value in data.get('dependencies', {}).items()
                if isinstance(value, dict) and value.get('optional')}
    pending = list(entry['features'])
    if entry['default_features'] and 'default' in definitions:
        pending.append('default')
    result = set()
    while pending:
        name = pending.pop()
        if name in result:
            continue
        if name not in definitions and name not in optional:
            raise ValueError(f'{data["package"]["name"]}: unknown provided feature {name}')
        result.add(name)
        pending.extend(item for item in definitions.get(name, [])
                       if not item.startswith('dep:') and '/' not in item)
    return result


def configure(data, registry, context):
    """Apply provider-owned features to the output copy of a consumer manifest.

    A consumer may require any subset of the published combination. Requests
    outside it are errors; Cargo must never expand the system provider silently.
    """
    data = deepcopy(data)
    for scope in [data, *data.get('target', {}).values()]:
        for alias, declaration in scope.get('dependencies', {}).items():
            name = declaration.get('package', alias) if isinstance(declaration, dict) else alias
            if name not in registry:
                continue
            _, entry, provider = registry[name]
            if isinstance(declaration, str):
                declaration = {'version': declaration}
                scope['dependencies'][alias] = declaration
            requested = set(declaration.get('features', []))
            if declaration.get('default-features', True) and 'default' in provider.get('features', {}):
                requested.add('default')
            unsupported = requested - feature_closure(provider, entry)
            if unsupported:
                raise ValueError(f'{context}: {name} does not provide features {sorted(unsupported)}; '
                                 'change the library component.json contract first')
            declaration['features'] = entry['features']
            declaration['default-features'] = entry['default_features']
    return data


def identities(graph, ignored=()):
    """Hash the actual compiler graph, independent of Cargo's unit numbering.

    This includes transitive versions, features, profiles, targets and build
    dependencies. Comparing top-level feature names alone cannot establish Rust
    dylib compatibility across independent Cargo resolutions.
    """
    if graph.get('version') != 1:
        raise ValueError('unsupported Cargo unit graph version')
    units, hashes, active = graph['units'], {}, set()

    def digest(index):
        if index in hashes:
            return hashes[index]
        if index in active:
            raise ValueError('cyclic Cargo compiler unit graph')
        active.add(index)
        unit = deepcopy(units[index])
        for edge in unit['dependencies']:
            edge['unit'] = digest(edge.pop('index'))
        unit['dependencies'].sort(key=lambda edge: json.dumps(edge, sort_keys=True))
        unit['features'].sort()
        result = hashlib.sha256(json.dumps(unit, sort_keys=True).encode()).hexdigest()
        active.remove(index)
        hashes[index] = result
        return result

    result = {}
    for index, unit in enumerate(units):
        if unit['mode'] == 'build' and 'dylib' in unit['target']['crate_types'] and unit['pkg_id'] not in ignored:
            name = unit['target']['name']
            if name in result:
                raise ValueError(f'multiple compiler identities for shared library {name}')
            result[name] = digest(index)
    return result


def verify(expected, actual, consumer):
    for name, identity in actual.items():
        if name not in expected:
            raise ValueError(f'{consumer}: unregistered shared library {name}')
        if expected[name] != identity:
            raise ValueError(f'{consumer}: incompatible shared library {name}; dependency versions, '
                             'features or compiler profile differ from its canonical provider')
