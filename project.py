#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Assemble isolated build inputs from a repo-managed component workspace.

Source repositories remain authoritative. The generated directory provides one
Cargo resolution/profile context for the Native dynamic-library closure and is
never a checkout developers should edit. It contains no fallback monorepo.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import tomllib

from toml_output import dumps as toml_dumps


HEADER = '# SPDX-FileCopyrightText: 2026 roolrz\n# SPDX-License-Identifier: Apache-2.0\n'
LICENSE = {'SPDX-FileCopyrightText': '2026 roolrz', 'SPDX-License-Identifier': 'Apache-2.0'}


def relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '.git' in path.parts:
        raise ValueError(f'invalid component path: {value!r}')
    return path


def run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, **kwargs)


def components(workspace):
    result = {}
    for metadata in sorted(workspace.glob('*/component.json')):
        value = json.loads(metadata.read_text())
        name = value['name']
        if name in result or metadata.parent.name != name:
            raise ValueError(f'duplicate or misplaced component: {name}')
        if value['version'] != 1:
            raise ValueError(f'unsupported component schema: {metadata}')
        if name == 'manifest':
            continue
        result[name] = (metadata.parent, value)
    if not {'HypeR', 'kernel', 'abi', 'toolchain', 'build', 'ci', 'bootstrap'} <= result.keys():
        raise ValueError('incomplete workspace; synchronize the manifest first')
    return result


def owned_sources(root, metadata):
    """Discover additions within declared domains without a per-file manifest edit."""
    sources = dict(metadata['sources'])
    mounts = metadata.get('mounts', {})
    candidates = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root
    ).decode().split('\0')
    for source in candidates:
        if not source or source in sources or source == 'component.json':
            continue
        if source.startswith('.github/'):
            continue
        for prefix, destination in sorted(mounts.items(), key=lambda item: -len(item[0])):
            if not prefix or source.startswith(prefix + '/'):
                suffix = source[len(prefix):].lstrip('/')
                sources[source] = str(PurePosixPath(destination) / suffix)
                break
    return sources


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.is_file() and not path.is_symlink() and path.read_bytes() == data
            and path.stat().st_mode & 0o777 == mode):
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.project-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def cargo_input(data, destination, specification):
    """Apply build membership without changing the owning repository's manifest."""
    value = tomllib.loads(data.decode())
    if specification['workspace'] is None:
        value.pop('workspace', None)
    else:
        workspace = value.setdefault('workspace', {})
        for member in specification.get('extra_workspace_members', []):
            if member not in workspace.setdefault('members', []):
                workspace['members'].append(member)
        for alias, target in specification.get('workspace_dependency_paths', {}).items():
            declaration = workspace['dependencies'][alias]
            if isinstance(declaration, str):
                declaration = {'version': declaration}
            declaration['path'] = os.path.relpath(target, str(PurePosixPath(destination).parent))
            workspace['dependencies'][alias] = declaration
    if specification['membership'] is not None:
        value['package']['workspace'] = specification['membership']
    contexts = {'': value}
    contexts.update({'target.' + key: item for key, item in value.get('target', {}).items()})
    inherited = specification.get('inherited_dependencies', {})
    for key in inherited:
        context, section, alias = key.rsplit('/', 2)
        dependencies = contexts.get(context, {}).get(section, {})
        if alias in dependencies:
            declaration = dependencies[alias]
            overrides = {k: v for k, v in declaration.items() if k not in ('path', 'version')} if isinstance(declaration, dict) else {}
            dependencies[alias] = {'workspace': True, **overrides}
    for key, target in specification['dependency_paths'].items():
        if key in inherited:
            continue
        context, section, alias = key.rsplit('/', 2)
        dependencies = contexts.get(context, {}).get(section, {})
        if alias not in dependencies:
            continue
        value_spec = dependencies[alias]
        if isinstance(value_spec, str):
            value_spec = dependencies[alias] = {'version': value_spec}
        value_spec['path'] = os.path.relpath(target, str(PurePosixPath(destination).parent))
    license_name = value.get('package', {}).get('license', 'Apache-2.0')
    header = HEADER.replace('SPDX-License-Identifier: Apache-2.0', f'SPDX-License-Identifier: {license_name}')
    return (header + '\n' + toml_dumps(value)).encode()


def prepare(workspace, output):
    workspace, output = workspace.resolve(), output.resolve()
    if output == workspace or output in workspace.parents:
        raise ValueError('output cannot contain the source workspace')
    marker = output / '.hyper-build.json'
    if output.exists() and any(output.iterdir()) and not marker.exists():
        raise ValueError(f'refusing unowned output directory: {output}')
    packages = components(workspace)
    files = {}
    inputs = {}
    for name, (root, metadata) in packages.items():
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        inputs[name] = {'commit': revision, 'files': {}, 'modes': {},
                        'metadata': hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()}
        for source, destination in sorted(owned_sources(root, metadata).items()):
            source_path = root / relative(source)
            destination = str(relative(destination))
            if destination in files:
                raise ValueError(f'duplicate source owner: {destination}')
            if not source_path.is_file():
                raise ValueError(f'missing declared source: {source_path}')
            if source_path.is_symlink():
                resolved = source_path.resolve()
                if not resolved.is_relative_to(root.resolve()):
                    raise ValueError(f'source symlink escapes component: {source_path}')
            data = source_path.read_bytes()
            inputs[name]['files'][source] = hashlib.sha256(data).hexdigest()
            inputs[name]['modes'][source] = source_path.stat().st_mode & 0o777
            if source in metadata.get('cargo_context', {}):
                data = cargo_input(data, destination, metadata['cargo_context'][source])
            files[destination] = (data, source_path.stat().st_mode & 0o777)
        # Component configuration is maintained with its source repository.
        if name not in ('kernel', 'bootstrap'):
            config = root / 'Kconfig'
            if config.exists():
                files[f'config/components/{name}.Kconfig'] = (config.read_bytes(), 0o644)
                inputs[name]['files']['Kconfig'] = hashlib.sha256(config.read_bytes()).hexdigest()
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    for domain, title in [('app', 'Applications'), ('svc', 'Services')]:
        group = 'application' if domain == 'app' else 'service'
        selected = [name for name, (_, item) in packages.items() if item['kind'] == group]
        text = HEADER + f'\nmenu "System / {title}"\n'
        text += ''.join(f'source "config/components/{name}.Kconfig"\n' for name in selected)
        files[f'{domain}/Kconfig'] = ((text + '\nendmenu\n').encode(), 0o644)
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    for stale in set(previous.get('files', [])) - files.keys():
        path = output / relative(stale)
        if path.is_file() or path.is_symlink():
            path.unlink()
    output.mkdir(parents=True, exist_ok=True)
    for path, (data, mode) in files.items():
        destination = output / path
        if not destination.resolve().is_relative_to(output):
            raise ValueError(f'output symlink escapes build directory: {destination}')
        if path in ('.hyper-build.json', 'source-inventory.json'):
            raise ValueError(f'reserved build output: {path}')
        atomic_write(destination, data, mode)
    inventory = {**LICENSE, 'version': 1, 'identity': identity, 'components': inputs}
    atomic_write(output / 'source-inventory.json', (json.dumps(inventory, indent=2) + '\n').encode())
    # Isolate Git-aware diagnostics and source checks from any enclosing checkout.
    if not (output / '.git').exists():
        run(['git', 'init', '-q', '-b', 'build'], output)
    run(['git', 'add', '-u'], output, stdout=subprocess.DEVNULL)
    declared = [*files, 'source-inventory.json']
    for offset in range(0, len(declared), 100):
        run(['git', 'add', '--', *declared[offset:offset + 100]], output, stdout=subprocess.DEVNULL)
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=output).returncode
    if changed:
        run(['git', '-c', 'user.name=HypeR Build', '-c', 'user.email=build@hyper.invalid',
             'commit', '-q', '-m', f'Assemble source inputs {identity[:16]}'], output)
    marker.write_text(json.dumps({**LICENSE, 'version': 1, 'files': sorted(files), 'identity': identity}) + '\n')
    return output


def module_tests(workspace, output, name):
    root = (workspace / relative(name)).resolve()
    if name == 'manifest' and not root.exists():
        root = (workspace / '.repo/manifests').resolve()
    metadata = json.loads((root / 'component.json').read_text())
    # Explicit commands belong to the component and execute against its resolved
    # dependency context. There is no implicit system/QEMU acceptance here.
    commands = metadata.get('tests', [])
    if not commands:
        raise ValueError(f'{name}: module test command has not been declared')
    for command in commands:
        run([argument.replace('{component}', str(root)).replace('{system}', str(output)) for argument in command], output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('prepare')
    test = actions.add_parser('test')
    test.add_argument('component')
    make = actions.add_parser('make')
    make.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    output = args.output or args.workspace / 'out/system'
    prepared = prepare(args.workspace, output)
    if args.action == 'test':
        module_tests(args.workspace, prepared, args.component)
    elif args.action == 'make':
        run(['make', *args.arguments], prepared)
    else:
        print(prepared)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
