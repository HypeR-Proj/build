# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Discover repository-owned module descriptions from the source manifest."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import xml.etree.ElementTree as ET

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')


def relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or {'..', '.git', '.repo'} & set(path.parts):
        raise ValueError(f'invalid module-relative path: {value!r}')
    return Path(path)


def manifest_path(root):
    for path in (root / '.repo/manifest.xml', root / '.repo/manifests/default.xml', root / 'manifest/default.xml',
                 root / '.workspace/default.xml'):
        if path.is_file():
            return path
    raise ValueError(f'no source manifest in {root}; initialize a repo workspace first')


def find_workspace(start):
    if value := os.environ.get('HYPER_WORKSPACE'):
        root = Path(value).resolve()
        manifest_path(root)
        return root
    start = Path(start).resolve()
    for path in (start, *start.parents):
        try:
            manifest_path(path)
            return path
        except ValueError:
            continue
    raise ValueError('cannot locate the workspace; set HYPER_WORKSPACE or initialize with repo')


@dataclass(frozen=True)
class Module:
    name: str
    source: Path
    path: Path
    metadata: dict
    nested: tuple = ()

    @property
    def build(self):
        return self.metadata.get('build', {})

    @property
    def configuration(self):
        return self.metadata.get('config', {'schema': 'Kconfig'} if (self.source / 'Kconfig').is_file() else None)

    @property
    def kconfig(self):
        entry = self.configuration
        if entry is None:
            return None
        if not isinstance(entry, dict) or not isinstance(entry.get('schema'), str):
            raise ValueError(f'{self.name}: config must declare a schema path')
        path = self.source / relative(entry['schema'])
        if not path.is_file():
            raise ValueError(f'{self.name}: missing configuration schema {path}')
        return path

    def files(self, output):
        result = None
        if (self.source / '.git').exists():
            result = subprocess.run(['git', '-C', str(self.source), 'ls-files', '-z',
                                     '--cached', '--others', '--exclude-standard'],
                                    capture_output=True, text=True)
        if result is None or result.returncode:
            # Exported source archives have no Git metadata. The exporter
            # supplies only source files; exclude generated directories here.
            names = []
            for base, directories, filenames in os.walk(self.source):
                base = Path(base)
                directories[:] = [name for name in directories
                                  if name not in ('.git', '.repo', 'out', 'target', '__pycache__')
                                  and base / name not in self.nested
                                  and not (base / name).resolve().is_relative_to(output)]
                names.extend(str((base / name).relative_to(self.source)) for name in filenames)
        else:
            names = result.stdout.split('\0')
        for name in sorted(set(names)):
            if not name:
                continue
            path = self.source / relative(name)
            if path.is_file() and not path.is_relative_to(output) and not path.resolve().is_relative_to(output):
                yield Path(name)


def read_manifest(path, seen=None):
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        raise ValueError(f'cyclic manifest include: {path}')
    seen.add(path)
    root = ET.parse(path).getroot()
    projects = list(root.findall('project'))
    for include in root.findall('include'):
        projects.extend(read_manifest(path.parent / relative(include.attrib['name']), seen))
    seen.remove(path)
    return projects


def discover(root):
    # repo's public manifest.xml is a link into its single manifest checkout.
    manifest = manifest_path(root).resolve()
    result, paths = {}, set()
    for project in read_manifest(manifest):
        name = project.attrib['name']
        path = relative(project.get('path', name))
        if not NAME.fullmatch(name) or name in result or path in paths:
            raise ValueError('duplicate or invalid manifest project')
        paths.add(path)
        source = root / path
        description = source / 'component.json'
        if not description.is_file():
            raise ValueError(f'{name}: missing component.json; run repo sync')
        data = json.loads(description.read_bytes())
        if data.get('version') != 1 or data.get('name') != name:
            raise ValueError(f'{name}: module description differs from the manifest')
        result[name] = Module(name, source, path, data)
    if 'manifest' not in result:
        data = json.loads((manifest.parent / 'component.json').read_bytes())
        result['manifest'] = Module('manifest', manifest.parent, Path('manifest'), data)
    if 'build' not in result:
        raise ValueError('manifest must include the build tools')
    return {name: replace(module, nested=tuple(other.source for other in result.values()
                                               if other.source != module.source
                                               and other.source.is_relative_to(module.source)))
            for name, module in result.items()}


def dependency_order(modules, selected):
    ordered, visiting, complete = [], set(), set()

    def visit(name):
        if name not in modules:
            raise ValueError(f'unknown module dependency: {name}')
        if name in visiting:
            raise ValueError(f'cyclic module dependency at {name}')
        if name in complete:
            return
        visiting.add(name)
        for dependency in modules[name].build.get('requires', []):
            visit(dependency)
        visiting.remove(name)
        complete.add(name)
        ordered.append(name)

    for name in selected:
        visit(name)
    return ordered
