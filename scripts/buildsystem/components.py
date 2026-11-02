# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Load explicit component includes without configuration or build inheritance."""

from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re


FIELDS = {'version', 'name', 'kind', 'include', 'config', 'build', 'cargo',
          'artifacts', 'tests', 'test_requires_rust', 'tests_need_workspace',
          'workspace', 'documentation', 'integration',
          'SPDX-FileCopyrightText', 'SPDX-License-Identifier'}
BUILD_FIELDS = {'type', 'requires', 'actions', 'steps', 'environment', 'makefile', 'source_archives'}
CARGO_FIELDS = {'manifest', 'domain', 'features', 'default_features', 'shared', 'link_probe'}


def relative_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError('component path must be a nonempty string')
    path = PurePosixPath(value)
    if path.is_absolute() or {'..', '.git', '.repo'} & set(path.parts):
        raise ValueError(f'invalid component path: {value}')
    return Path(path)


def string_list(value, context):
    if (not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f'{context}: expected unique nonempty strings')


def validate(data, context, *, fragment=False):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError(f'{context}: unsupported component schema')
    if unknown := data.keys() - FIELDS:
        raise ValueError(f'{context}: unknown component fields: {sorted(unknown)}')
    if fragment and {'name', 'kind'} & data.keys():
        raise ValueError(f'{context}: fragments cannot redefine their repository identity')
    if not isinstance(data.get('include', []), list):
        raise ValueError(f'{context}: include must be an array')
    build = data.get('build', {})
    if not isinstance(build, dict) or build.keys() - BUILD_FIELDS:
        raise ValueError(f'{context}: invalid build fields')
    if build.get('type') not in (None, 'native-rust', 'group'):
        raise ValueError(f'{context}: unknown build type')
    if build.get('type') and {'actions', 'steps', 'makefile'} & build.keys():
        raise ValueError(f'{context}: build type cannot also declare a custom backend')
    if 'requires' in build:
        string_list(build['requires'], f'{context}: build.requires')
    if 'source_archives' in build:
        archives = build['source_archives']
        if (build.get('type') not in (None, 'native-rust') or not isinstance(archives, dict)
                or archives.keys() != {'manifest', 'cache', 'cache_env'}):
            raise ValueError(f'{context}: source_archives requires a Native manifest, cache and cache_env')
        relative_path(archives['manifest'])
        relative_path(archives['cache'])
        if not isinstance(archives['cache_env'], str) or not re.fullmatch(r'HYPER_[A-Z0-9_]+', archives['cache_env']):
            raise ValueError(f'{context}: source archive cache_env must name a HYPER_ environment variable')
    if not isinstance(data.get('cargo', []), list):
        raise ValueError(f'{context}: cargo must be an array')
    for entry in data.get('cargo', []):
        if not isinstance(entry, dict) or entry.keys() - CARGO_FIELDS:
            raise ValueError(f'{context}: invalid cargo entry')
        relative_path(entry.get('manifest'))
        if entry.get('domain') not in ('boot', 'system'):
            raise ValueError(f'{context}: cargo domain must be boot or system')
        if 'features' in entry:
            string_list(entry['features'], f'{context}: cargo.features')
        if 'default_features' in entry and not isinstance(entry['default_features'], bool):
            raise ValueError(f'{context}: default_features must be boolean')
        if 'shared' in entry and not isinstance(entry['shared'], bool):
            raise ValueError(f'{context}: shared must be boolean')
        if entry.get('shared') and not {'features', 'default_features'} <= entry.keys():
            raise ValueError(f'{context}: shared providers must fix features and default_features')
        if 'link_probe' in entry:
            relative_path(entry['link_probe'])
            if not entry.get('shared'):
                raise ValueError(f'{context}: only a shared provider may declare a link_probe')


def merge(destination, source, context, *, root=True):
    """Compose declarations; an include can never silently replace a definition."""
    for key, value in source.items():
        if root and key in ('include', 'version', 'SPDX-FileCopyrightText', 'SPDX-License-Identifier'):
            continue
        if key not in destination:
            destination[key] = deepcopy(value)
        elif isinstance(value, list) and isinstance(destination[key], list):
            destination[key].extend(deepcopy(value))
        elif isinstance(value, dict) and isinstance(destination[key], dict):
            merge(destination[key], value, f'{context}.{key}', root=False)
        else:
            raise ValueError(f'{context}: duplicate definition of {key}')


def load(repositories, entry='build'):
    """Return resolved metadata and include inputs, keyed by manifest project ID.

    Local includes compose one repository. Project includes register another
    owner, without merging its configuration, environment or recipes.
    """
    resolved, inputs, active, complete = {}, {}, [], set()
    roots = {name: Path(path).resolve() for name, path in repositories.items()}

    def visit(project, filename='component.json', *, fragment=False):
        if project not in roots:
            raise ValueError(f'include references unknown manifest project: {project}')
        source = roots[project] / relative_path(filename)
        key = project, str(relative_path(filename))
        if key in active:
            chain = ' -> '.join(f'{owner}:{path}' for owner, path in [*active, key])
            raise ValueError(f'cyclic component include: {chain}')
        if key in complete:
            return
        if (not source.resolve().is_relative_to(roots[project])
                or any(name != project and root.is_relative_to(roots[project])
                       and source.resolve().is_relative_to(root) for name, root in roots.items())):
            raise ValueError(f'{project}:{filename}: include crosses repository ownership')
        if not source.is_file():
            raise ValueError(f'{project}:{filename}: missing component include')
        data = json.loads(source.read_text())
        validate(data, f'{project}:{filename}', fragment=fragment)
        if not fragment:
            if data.get('name') != project:
                raise ValueError(f'{project}: component name differs from manifest project')
            if project in resolved:
                raise ValueError(f'{project}: multiple component entry points')
            resolved[project] = {'version': 1}
        merge(resolved[project], data, f'{project}:{filename}')
        inputs.setdefault(project, []).append(str(source))
        active.append(key)
        for include in data.get('include', []):
            if isinstance(include, str):
                local = relative_path(filename).parent / relative_path(include)
                visit(project, str(local), fragment=True)
            elif isinstance(include, dict) and set(include) <= {'project', 'file'} and 'project' in include:
                if not isinstance(include['project'], str):
                    raise ValueError(f'{project}:{filename}: project name must be a string')
                visit(include['project'], include.get('file', 'component.json'))
                declaration = {'project': include['project']}
                if declaration not in resolved[project].setdefault('include', []):
                    resolved[project]['include'].append(declaration)
            else:
                raise ValueError(f'{project}:{filename}: invalid include entry')
        active.pop()
        complete.add(key)

    for project in [entry] if isinstance(entry, str) else entry:
        visit(project)
    if 'manifest' in roots and 'manifest' not in resolved:
        visit('manifest')
    for name, data in resolved.items():
        validate(data, name)
        recipe = data.get('build', {})
        if 'source_archives' in recipe and recipe.get('type') != 'native-rust':
            raise ValueError(f'{name}: source archives require a native-rust build')
        manifests = [entry['manifest'] for entry in data.get('cargo', [])]
        if len(manifests) != len(set(manifests)):
            raise ValueError(f'{name}: duplicate Cargo manifest declaration')
    return resolved, inputs
