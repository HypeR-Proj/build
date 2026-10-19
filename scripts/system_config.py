# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Product selection over the component registry, independent of board hardware."""

import hashlib
import json
from pathlib import Path
import re
import tomllib


DEFAULT_BOOT = ('init', 'console-raw', 'fallback-sh', 'io-runtime', 'fs-backend',
                'fs-erofs', 'fs-fat', 'mount', 'umount')
HEADERS = {'SPDX-FileCopyrightText', 'SPDX-License-Identifier'}
KERNEL_MANIFEST = Path(__file__).resolve().parents[1] / 'kernel/Cargo.toml'


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'duplicate product configuration key: {key}')
        value[key] = item
    return value


def names(value, context, *, features=False):
    pattern = r'[a-zA-Z0-9_-]+' if features else r'[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*'
    if (not isinstance(value, list)
            or any(not isinstance(name, str) or not re.fullmatch(pattern, name)
                   for name in value)
            or len(value) != len(set(value))):
        raise ValueError(f'{context} must be a list of unique component or feature names')
    return sorted(value)


class ProductConfig:
    def __init__(self, source):
        if (not isinstance(source, dict) or source.keys() - HEADERS - {'version', 'boot', 'system', 'kernel'}
                or type(source.get('version')) is not int or source['version'] != 1):
            raise ValueError('product configuration requires version 1 and boot/system/kernel sections')
        self.value = {'version': 1}
        for environment in ('boot', 'system'):
            section = source.get(environment, {})
            if (not isinstance(section, dict) or section.keys() - {'base', 'include', 'exclude'}
                    or section.get('base', 'default') not in ('default', 'none')):
                raise ValueError(f'invalid {environment} selection; base must be default or none')
            include = names(section.get('include', []), f'{environment}.include')
            exclude = names(section.get('exclude', []), f'{environment}.exclude')
            if set(include) & set(exclude):
                raise ValueError(f'{environment} includes and excludes the same component')
            self.value[environment] = {'base': section.get('base', 'default'),
                                       'include': include, 'exclude': exclude}
        if 'kernel' in source:
            kernel = source['kernel']
            if not isinstance(kernel, dict) or kernel.keys() != {'features'}:
                raise ValueError('kernel selection requires exactly features')
            features = names(kernel['features'], 'kernel.features', features=True)
            manifest = tomllib.loads(KERNEL_MANIFEST.read_text())
            allowed = set(manifest['package']['metadata']['hyper']['production-features'])
            if allowed - manifest['features'].keys():
                raise ValueError('kernel production feature metadata names undeclared Cargo features')
            if invalid := set(features) - allowed:
                raise ValueError(f'not production kernel features: {sorted(invalid)}; '
                                 f'available features: {sorted(allowed)}')
            self.value['kernel'] = {'features': features}

    def identity(self):
        canonical = json.dumps(self.value, sort_keys=True, separators=(',', ':')).encode()
        return hashlib.sha256(canonical).hexdigest()[:16]

    def selected_names(self, entries, environment, profile='system', boot_components=None):
        available = {entry['staged']: entry for entry in entries if 'binary' in entry
                     and entry.get('environment', 'system') == environment}
        section = self.value[environment]
        requested = set(section['include']) | set(section['exclude'])
        if missing := requested - available.keys():
            raise ValueError(f'unknown {environment} components: {sorted(missing)}')
        selected = set()
        if section['base'] == 'default':
            if environment == 'boot':
                selected = set(DEFAULT_BOOT if boot_components is None else boot_components)
                if missing := selected - available.keys():
                    raise ValueError(f'unknown boot components: {sorted(missing)}')
            else:
                selected = {name for name, entry in available.items() if profile in entry['profiles']}
        selected |= set(section['include'])
        selected -= set(section['exclude'])
        return selected

    def boot_components(self, entries, override=None):
        return sorted(self.selected_names(entries, 'boot', boot_components=override))

    def select(self, entries, profile='system', boot_components=None):
        selected = {environment: self.selected_names(entries, environment, profile, boot_components)
                    for environment in ('boot', 'system')}
        result = [entry for entry in entries
                  if (entry['staged'] in selected[entry.get('environment', 'system')]
                      if 'binary' in entry else profile in entry['profiles'])]
        available = set().union(*selected.values())
        for entry in result:
            for required in entry.get('runtime_requires', []):
                if required not in available:
                    raise ValueError(f"component {entry['staged']} requires deployed component {required}")
        return result


def load(path):
    return ProductConfig(json.loads(Path(path).read_text(), object_pairs_hook=unique_object))
