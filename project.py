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
import shutil
import subprocess
import sys
import tempfile
import tomllib


HEADER = '# SPDX-FileCopyrightText: 2026 roolrz\n# SPDX-License-Identifier: Apache-2.0\n'


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
        result[name] = (metadata.parent, value)
    if not {'HypeR', 'kernel', 'abi', 'toolchain', 'build', 'ci', 'bootstrap'} <= result.keys():
        raise ValueError('incomplete workspace; synchronize the manifest first')
    return result


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
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
        inputs[name] = {'commit': revision, 'files': {}}
        for source, destination in sorted(metadata['sources'].items()):
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
            files[destination] = (data, source_path.stat().st_mode & 0o777)
        # Component configuration is maintained with its source repository.
        if name not in ('kernel', 'bootstrap'):
            config = root / 'Kconfig'
            if config.exists():
                files[f'config/components/{name}.Kconfig'] = (config.read_bytes(), 0o644)
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
        atomic_write(output / path, data, mode)
    inventory = {'version': 1, 'identity': identity, 'components': inputs}
    atomic_write(output / 'source-inventory.json', (json.dumps(inventory, indent=2) + '\n').encode())
    # Isolate Git-aware diagnostics and source checks from any enclosing checkout.
    if not (output / '.git').exists():
        run(['git', 'init', '-q', '-b', 'build'], output)
    run(['git', 'add', '--all'], output, stdout=subprocess.DEVNULL)
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=output).returncode
    if changed:
        run(['git', '-c', 'user.name=HypeR Build', '-c', 'user.email=build@hyper.invalid',
             'commit', '-q', '-m', f'Assemble source inputs {identity[:16]}'], output)
    marker.write_text(json.dumps({'version': 1, 'files': sorted(files), 'identity': identity}) + '\n')
    return output


def module_tests(workspace, output, name):
    root, metadata = components(workspace)[name]
    # Explicit commands belong to the component and execute against its resolved
    # dependency context. There is no implicit system/QEMU acceptance here.
    commands = metadata.get('tests', [])
    if not commands:
        raise ValueError(f'{name}: module test command has not been declared')
    for command in commands:
        run(command, output)


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
    output = args.output or args.workspace.parent / 'out/system'
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
