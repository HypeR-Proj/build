# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Render module documentation from the manifest, without a second module list."""

import html
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')


def local(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or {'..', '.git', '.repo'} & set(path.parts):
        raise ValueError(f'invalid catalog source: {value!r}')
    return path


def render(root, filename, metadata):
    projects, visited = {}, set()

    def read(filename):
        path = root / local(filename)
        if not path.resolve().is_relative_to(root.resolve()) or path.resolve() in visited:
            raise ValueError('catalog includes must remain local and acyclic')
        visited.add(path.resolve())
        tree = ET.parse(path).getroot()
        for project in tree.findall('project'):
            name = project.attrib['name']
            if not NAME.fullmatch(name) or name in projects:
                raise ValueError('invalid or duplicate catalog repository')
            projects[name] = str(local(project.get('path', name)))
        for include in tree.findall('include'):
            read(str(local(filename).parent / local(include.attrib['name'])))
        visited.remove(path.resolve())

    read(filename)
    projects.setdefault('manifest', '.repo/manifests')
    for name in metadata.get('integration', {}).get('repositories', []):
        if not NAME.fullmatch(name):
            raise ValueError('invalid integration repository name')
        projects.setdefault(name, 'not part of the source checkout')
    lines = ['# Module documentation', '',
             f'Generated from [{filename}]({filename}) when the manifest documentation is published.',
             'Each repository publishes its own documentation after changes merge into `main`.', '',
             '| Repository | Workspace path | Documentation | Source |',
             '| --- | --- | --- | --- |']
    for name, path in sorted(projects.items(), key=lambda item: item[0].lower()):
        lines.append(f'| {name} | <code>{html.escape(path)}</code> | '
                     f'[Read](https://hyper-proj.github.io/{name}/) | '
                     f'[GitHub](https://github.com/HypeR-Proj/{name}) |')
    return '\n'.join(lines) + '\n'
