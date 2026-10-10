# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Read component templates and check their declared Cargo delivery targets."""

import os
from pathlib import Path
import subprocess
import tomllib

from cargo_workspace import member_directories


ROOT = Path(__file__).resolve().parents[1]


def load_registry(path):
    # Use Make itself to expand the templates, rather than interpreting a second
    # subset of Make syntax. Do not inherit a parent's dry-run or jobserver state.
    env = dict(os.environ)
    for name in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES'):
        env.pop(name, None)
    result = subprocess.run(
        ['make', '--no-print-directory', '-s', '-f', str(Path(path).resolve()),
         'component-records'], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    members = member_directories(ROOT / 'app/Cargo.toml')
    entries = []
    for record in result.stdout.splitlines():
        fields = record.split('|')
        kind = fields[0]
        if kind in ('binary', 'library') and len(fields) == 6:
            _, source, artifact, staged, destination, profiles = fields
            directory = (ROOT / source).resolve()
            if directory not in members:
                raise ValueError(f'component is not an app workspace member: {source}')
            manifest = tomllib.loads((directory / 'Cargo.toml').read_text())
            if kind == 'binary':
                if artifact not in {item['name'] for item in manifest.get('bin', [])}:
                    raise ValueError(f'component binary is absent from Cargo manifest: {artifact}')
            else:
                library = manifest.get('lib', {})
                name = library.get('name', manifest['package']['name'].replace('-', '_'))
                if 'dylib' not in library.get('crate-type', []) or artifact != f'lib{name}.so':
                    raise ValueError(f'component is not the declared Rust dylib: {artifact}')
            entry = {kind: artifact, 'staged': staged, 'destination': destination,
                     'mode': '0755', 'profiles': profiles.split(),
                     'package': manifest['package']['name']}
        elif kind in ('source', 'symlink') and len(fields) == 5:
            _, source, destination, mode, profiles = fields
            entry = {kind: source, 'destination': destination, 'mode': mode,
                     'profiles': profiles.split()}
        else:
            raise ValueError(f'invalid component record: {record}')
        entries.append(entry)
    return {'version': 1, 'entries': entries}
