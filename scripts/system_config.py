# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Product selection over the component registry, independent of board hardware."""

import hashlib
import json
from pathlib import Path
import re
import tomllib

import boot_profiles
import kconfig


HEADERS = {'SPDX-FileCopyrightText', 'SPDX-License-Identifier'}
ROOT = Path(__file__).resolve().parents[1]
KERNEL_MANIFEST = ROOT / 'kernel/Cargo.toml'


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
        self.policy_path = ''
        if (not isinstance(source, dict) or source.keys() - HEADERS - {'version', 'boot', 'system', 'kernel', 'boot-profiles'}
                or type(source.get('version')) is not int or source['version'] != 1):
            raise ValueError('product configuration requires version 1 and boot/system/kernel sections')
        self.value = {'version': 1}
        for environment in ('boot', 'system'):
            section = source.get(environment, {})
            allowed = {'base', 'include', 'exclude'} | ({'storage-providers'} if environment == 'system' else set())
            if (not isinstance(section, dict) or section.keys() - allowed
                    or section.get('base', 'default') not in ('default', 'none')):
                raise ValueError(f'invalid {environment} selection; base must be default or none')
            include = names(section.get('include', []), f'{environment}.include')
            exclude = names(section.get('exclude', []), f'{environment}.exclude')
            if set(include) & set(exclude):
                raise ValueError(f'{environment} includes and excludes the same component')
            self.value[environment] = {'base': section.get('base', 'default'),
                                       'include': include, 'exclude': exclude}
            if 'storage-providers' in section:
                providers = section['storage-providers']
                if not isinstance(providers, list) or len(providers) > boot_profiles.MAX_PROFILES:
                    raise ValueError('system.storage-providers must list at most 16 formal storage providers')
                selected = []
                for provider in providers:
                    if not isinstance(provider, dict) or provider.keys() != {'component', 'image'}:
                        raise ValueError('formal storage provider requires component and image')
                    selected.append({
                        'component': boot_profiles.component_path(provider['component']),
                        'image': boot_profiles.absolute_path(provider['image'], 'formal storage image'),
                    })
                if len({provider['component'] for provider in selected}) != len(selected):
                    raise ValueError('duplicate formal storage provider component')
                self.value[environment]['storage-providers'] = sorted(selected, key=lambda provider: provider['component'])
        if 'boot-profiles' in source:
            self.value['boot-profiles'] = boot_profiles.validate_set(source['boot-profiles'])
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
            if environment == 'boot' and boot_components is not None:
                selected = set(boot_components)
                if missing := selected - available.keys():
                    raise ValueError(f'unknown boot components: {sorted(missing)}')
            else:
                # Both environments derive defaults from domain Kconfig. Keep no
                # second bootstrap list that can drift from the menu/registry.
                # Formal fixture profiles must not remove bootstrap providers.
                membership = 'system' if environment == 'boot' else profile
                selected = {name for name, entry in available.items() if membership in entry['profiles']}
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


def component_symbols(entries):
    """Bind Kconfig program symbols to their registered delivery targets."""
    symbols = {}
    for entry in entries:
        if 'binary' not in entry:
            continue
        scope = entry.get('environment', 'system')
        name = entry['staged']
        symbol = 'CONFIG_' + scope.upper() + '_APP_' + re.sub(r'[^A-Za-z0-9]', '_', name).upper()
        if symbol in symbols:
            raise ValueError(f'configuration symbol collision: {symbol}')
        symbols[symbol] = (scope, name)
    return symbols


def registry_entries():
    from native_components import load_registry
    return load_registry(ROOT / 'mk/components.mk')['entries']


def quoted(value):
    if any(ord(char) < 32 for char in value):
        raise ValueError('configuration strings cannot contain control characters')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def resolve(values, entries):
    symbols = component_symbols(entries)
    source = {'version': 1, 'boot': {'base': 'none', 'include': []},
              'system': {'base': 'none', 'include': []}}
    for key, value in values.items():
        if key in ('CONFIG_KERNEL_FEATURES', 'CONFIG_BOOT_POLICY'):
            continue
        if key not in symbols:
            raise ValueError(f'configuration has no registered program: {key}')
        if value not in ('y', 'n'):
            raise ValueError(f'{key}: expected y or n')
        if value == 'y':
            scope, name = symbols[key]
            source[scope]['include'].append(name)
    features = values.get('CONFIG_KERNEL_FEATURES', kconfig.schema()['CONFIG_KERNEL_FEATURES']['default'])
    if features != 'default':
        source['kernel'] = {'features': features.split(',') if features else []}
    policy = values.get('CONFIG_BOOT_POLICY', kconfig.schema()['CONFIG_BOOT_POLICY']['default'])
    if policy:
        path = Path(policy)
        # Paths are repository-relative, independent of where .config is saved.
        # This also makes a savedefconfig export portable within the repository.
        data = json.loads((path if path.is_absolute() else ROOT / path).read_text(),
                          object_pairs_hook=unique_object)
        if not isinstance(data, dict) or data.keys() - HEADERS - {'boot-profiles', 'storage-providers'}:
            raise ValueError('boot policy accepts only boot-profiles and storage-providers')
        if 'boot-profiles' in data:
            source['boot-profiles'] = data['boot-profiles']
        if 'storage-providers' in data:
            source['system']['storage-providers'] = data['storage-providers']
    product = ProductConfig(source)
    product.policy_path = policy
    return product


def load(path, entries=None, *, migrate=False):
    entries = registry_entries() if entries is None else entries
    result = kconfig.query('migrate' if migrate else 'resolve', Path(path).resolve())
    known = set(component_symbols(entries)) | {'CONFIG_KERNEL_FEATURES', 'CONFIG_BOOT_POLICY'}
    if invalid := set(result['assigned']) - set(result['removed']) - known:
        raise ValueError(f'configuration has no registered programs: {sorted(invalid)}')
    return resolve({name: value for name, value in result['values'].items() if name in known}, entries)


def format_config(product, entries, *, minimal=False):
    """Emit a resolved .config or a defconfig containing only default overrides."""
    symbols = component_symbols(entries)
    # Check names even if no dependency validation was requested by the caller.
    selected = {scope: product.selected_names(entries, scope) for scope in ('boot', 'system')}
    settings = kconfig.schema()
    lines = ['# SPDX-FileCopyrightText: 2026 roolrz', '# SPDX-License-Identifier: Apache-2.0', '']
    for scope in ('boot', 'system'):
        lines.append('# ' + ('Bootstrap stage1' if scope == 'boot' else 'System'))
        for symbol, (environment, name) in sorted(symbols.items()):
            if environment != scope:
                continue
            enabled = name in selected[scope]
            if not minimal or enabled != (settings[symbol]['default'] == 'y'):
                lines.append(f'{symbol}=y' if enabled else f'# {symbol} is not set')
        lines.append('')
    kernel = product.value.get('kernel')
    if not minimal or kernel is not None:
        features = ','.join(kernel['features']) if kernel is not None else 'default'
        lines.append('CONFIG_KERNEL_FEATURES=' + quoted(features))
    if not minimal or product.policy_path:
        lines.append('CONFIG_BOOT_POLICY=' + quoted(product.policy_path))
    if not product.policy_path and ('boot-profiles' in product.value
                                    or 'storage-providers' in product.value['system']):
        raise ValueError('structured boot policy requires CONFIG_BOOT_POLICY')
    return '\n'.join(lines) + '\n'
