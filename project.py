#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Inspect, prepare and test a manifest workspace without generated source files."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent / 'scripts'))
from buildsystem import files
from buildsystem.model import discover, manifest_path, relative
from buildsystem.prepare import prepare as prepare_view

LICENSE = {'SPDX-FileCopyrightText': '2026 roolrz', 'SPDX-License-Identifier': 'Apache-2.0'}


def manifest(workspace):
    return ET.parse(manifest_path(workspace)).getroot()


def components(workspace):
    return {name: (module.source, module.metadata) for name, module in discover(workspace).items()}


def git(root, *arguments):
    return subprocess.check_output(['git', *arguments], cwd=root, text=True).strip()


def output_path(workspace, output=None):
    return Path(output or os.environ.get('OUT', workspace / 'out')).resolve()


def source_files(workspace, output=None):
    """Enumerate owners once, including the implicit repo manifest checkout."""
    output = output_path(workspace, output)
    return {str(module.path / name): module.source / name
            for module in discover(workspace).values() for name in module.files(output)}


def prepare(workspace, output=None):
    view, _, _, _ = prepare_view(workspace.resolve(), output_path(workspace, output),
                                os.environ.get('ARCH', 'aarch64'))
    return view


def inventory(workspace, output, excluded=()):
    inputs = {}
    exported = workspace / 'source-inventory.json'
    provenance = json.loads(exported.read_bytes()).get('components', {}) if exported.exists() else {}
    modules = discover(workspace)
    selected = str(modules['manifest'].path / manifest_path(workspace).resolve().relative_to(modules['manifest'].source.resolve()))
    for name, module in modules.items():
        content = {str(file): hashlib.sha256((module.source / file).read_bytes()).hexdigest()
                   for file in module.files(output) if (module.source / file).resolve() not in (*excluded, exported)}
        commit = (git(module.source, 'rev-parse', 'HEAD') if (module.source / '.git').exists()
                  else provenance.get(name, {}).get('commit'))
        filters = ['.'] + [':(exclude,literal)' + str(path.relative_to(module.source))
                          for path in (*excluded, exported, output) if path.is_relative_to(module.source)]
        dirty = (bool(git(module.source, 'status', '--porcelain=v1', '--untracked-files=all', '--', *filters))
                 if (module.source / '.git').exists() else
                 bool(provenance.get(name, {}).get('dirty')
                      or content != provenance.get(name, {}).get('files', content)))
        inputs[name] = {'commit': commit, 'dirty': dirty,
                        'path': str(module.path), 'files': content}
    identity = hashlib.sha256(json.dumps({'components': inputs, 'manifest': selected}, sort_keys=True).encode()).hexdigest()
    return {**LICENSE, 'version': 1, 'identity': identity, 'components': inputs, 'manifest': selected}


def archive(workspace, destination, output=None):
    workspace, destination = workspace.resolve(), destination.resolve()
    output = output_path(workspace, output)
    record = inventory(workspace, output, excluded=(destination,))
    source = source_files(workspace, output)
    path = output / 'state/source-inventory.json'
    files.write(path, json.dumps(record, indent=2) + '\n')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.source-archive-', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with tarfile.open(temporary, 'w:xz', dereference=True) as stream:
            for name, body in sorted(source.items()):
                if body.resolve() != destination:
                    stream.add(body, arcname='HypeR/' + name, recursive=False)
            stream.add(path, arcname='HypeR/source-inventory.json')
        if inventory(workspace, output, excluded=(destination, temporary)) != record:
            raise ValueError('sources changed during archive construction; retry')
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def module_tests(workspace, name, output=None):
    if os.environ.get('HYPER_BUILD_VIEW') == '1' and str(workspace) == os.environ.get('HYPER_BUILD_VIEW_PATH'):
        module = discover(workspace)[name]
        for command in module.metadata.get('tests', []):
            subprocess.run([value.replace('{component}', str(module.source)).replace('{system}', str(workspace))
                            for value in command], cwd=workspace, check=True)
        return
    command = [sys.executable, str(workspace / 'scripts/build.py'), '--workspace', str(workspace),
               '--out', str(output_path(workspace, output)), '--module', name, 'test']
    subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--out', type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('prepare')
    actions.add_parser('files')
    test = actions.add_parser('test')
    test.add_argument('component')
    pack = actions.add_parser('archive')
    pack.add_argument('output', type=Path)
    actions.add_parser('inventory')
    execute = actions.add_parser('exec')
    execute.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if args.action == 'test':
        module_tests(workspace, args.component, args.out)
    elif args.action == 'files':
        print('\n'.join(sorted(source_files(workspace, args.out))))
    elif args.action == 'archive':
        archive(workspace, args.output, args.out)
    elif args.action == 'inventory':
        output = output_path(workspace, args.out)
        path = output / 'state/source-inventory.json'
        files.write(path, json.dumps(inventory(workspace, output), indent=2) + '\n')
        print(path)
    elif args.action == 'exec':
        subprocess.run([sys.executable, str(workspace / 'scripts/build.py'), '--workspace', str(workspace),
                        '--out', str(output_path(workspace, args.out)), 'exec', *args.command], check=True)
    else:
        print(prepare(workspace, args.out))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'project: {error}')
