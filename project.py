#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Inspect and test a workspace laid out directly by the repo manifest."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import xml.etree.ElementTree as ET

LICENSE = {'SPDX-FileCopyrightText': '2026 roolrz', 'SPDX-License-Identifier': 'Apache-2.0'}


def relative(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts
            or any(part in ('.git', '.repo') for part in path.parts)):
        raise ValueError(f'invalid workspace path: {value!r}')
    return path


def manifest(workspace):
    path = workspace / '.workspace/default.xml'
    if not path.is_file():
        path = workspace / '.repo/manifests/default.xml'
    return ET.parse(path).getroot()


def components(workspace):
    result = {}
    paths = set()
    for item in manifest(workspace).findall('project'):
        name = item.attrib['name']
        path = str(relative(item.get('path', name)))
        if name in result or path in paths:
            raise ValueError('duplicate manifest project or checkout path')
        paths.add(path)
        root = workspace / path
        metadata = json.loads((root / 'component.json').read_text())
        if metadata['name'] != name or metadata['version'] != 1:
            raise ValueError(f'component metadata differs from manifest: {name}')
        result[name] = (root, metadata)
    return result


def git(root, *arguments):
    return subprocess.check_output(['git', *arguments], cwd=root, text=True).strip()


def source_files(workspace):
    """List tracked sources across projects, including manifest-provided links."""
    result = {}
    for item in manifest(workspace).findall('project'):
        root = workspace / relative(item.get('path', item.attrib['name']))
        for name in git(root, 'ls-files', '-z').split('\0'):
            if not name:
                continue
            source = root / relative(name)
            if source.is_file():
                result[str(source.relative_to(workspace))] = source
        for link in list(item.findall('linkfile')) + list(item.findall('copyfile')):
            source = root / relative(link.attrib['src'])
            destination = relative(link.attrib['dest'])
            if source.is_dir():
                for name in git(root, 'ls-files', '-z', '--', link.attrib['src']).split('\0'):
                    if name and (root / name).is_file():
                        result[str(destination / (root / name).relative_to(source))] = root / name
            else:
                result[str(destination)] = source
    return result


def prepare(workspace):
    """Record provenance in place; never copy, rewrite, or commit source files."""
    workspace = workspace.resolve()
    inputs = {}
    for name, (root, _) in components(workspace).items():
        files = {}
        for file in git(root, 'ls-files', '-z').split('\0'):
            if file and (root / file).is_file():
                files[file] = hashlib.sha256((root / file).read_bytes()).hexdigest()
        inputs[name] = {'commit': git(root, 'rev-parse', 'HEAD'),
                        'path': str(root.relative_to(workspace)), 'files': files}
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    inventory = {**LICENSE, 'version': 1, 'identity': identity, 'components': inputs}
    (workspace / 'source-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    return workspace


def archive(workspace, output):
    prepare(workspace)
    with tarfile.open(output, 'w:xz', dereference=True) as stream:
        for destination, source in sorted(source_files(workspace).items()):
            stream.add(source, arcname='HypeR/' + destination, recursive=False)
        stream.add(workspace / 'source-inventory.json', arcname='HypeR/source-inventory.json')


def module_tests(workspace, name):
    root, metadata = components(workspace)[name]
    commands = metadata.get('tests', [])
    if not commands:
        raise ValueError(f'{name}: module tests are not declared')
    for command in commands:
        subprocess.run([arg.replace('{component}', str(root)).replace('{system}', str(workspace))
                        for arg in command], cwd=workspace, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parent)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('prepare')
    actions.add_parser('files')
    test = actions.add_parser('test')
    test.add_argument('component')
    pack = actions.add_parser('archive')
    pack.add_argument('output', type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if args.action == 'test':
        module_tests(workspace, args.component)
    elif args.action == 'files':
        print('\n'.join(sorted(source_files(workspace))))
    elif args.action == 'archive':
        archive(workspace, args.output)
    else:
        print(prepare(workspace))


if __name__ == '__main__':
    main()
