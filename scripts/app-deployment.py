#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Install and compose applications from one deployment manifest."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


def archive_path(name):
    path = PurePosixPath(name)
    if not name or name == '.' or '\0' in name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError(f'invalid deployment path: {name}')
    return name


def load(path):
    manifest = json.loads(Path(path).read_text())
    if manifest['version'] != 1:
        raise ValueError('unsupported application deployment version')
    destinations, artifacts, staged = set(), set(), set()
    entries = manifest['entries']
    for entry in entries:
        name = archive_path(entry['destination'])
        if name in destinations:
            raise ValueError(f'duplicate deployment destination: {name}')
        destinations.add(name)
        if not re.fullmatch(r'0[0-7]{3}', entry['mode']):
            raise ValueError(f'invalid deployment mode: {name}')
        if not entry['profiles'] or set(entry['profiles']) - {'system', 'development', 'io'}:
            raise ValueError(f'invalid deployment profiles: {name}')
        if sum(key in entry for key in ('binary', 'library', 'source', 'symlink')) != 1:
            raise ValueError(f'deployment needs exactly one source: {name}')
        if 'symlink' in entry:
            archive_path(entry['symlink'])
            if entry['mode'] != '0777':
                raise ValueError(f'symlink mode must be 0777: {name}')
        if 'binary' in entry or 'library' in entry:
            artifact = entry.get('binary', entry.get('library'))
            location = archive_path(entry['staged'])
            pattern = r'[a-zA-Z0-9_-]+' if 'binary' in entry else r'lib[a-zA-Z0-9_-]+\.so'
            if not re.fullmatch(pattern, artifact) or artifact in artifacts or location in staged:
                raise ValueError(f'duplicate or invalid application artifact: {artifact}')
            artifacts.add(artifact); staged.add(location)
    return entries


def compose(manifest, profile, roots, replacements=()):
    entries = []
    overrides = {}
    for replacement in replacements:
        name, separator, source = replacement.partition('=')
        if not separator or not source or name in overrides:
            raise ValueError(f'invalid deployment replacement: {replacement}')
        overrides[name] = source
    for item in load(manifest):
        if profile not in item['profiles']:
            continue
        name = archive_path(item['destination'].format_map(roots))
        if 'symlink' in item:
            entries.extend(['symlink', name, archive_path(item['symlink'].format_map(roots))])
            continue
        source = (str(Path(roots['apps']) / item['staged']) if 'source' not in item
                  else item['source'].format_map(roots))
        entries.extend([item['mode'], name, overrides.pop(name, source)])
    if overrides:
        raise ValueError(f'replacements not in selected profile: {sorted(overrides)}')
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['binaries', 'install'])
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--build', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    entries = load(args.manifest)
    if args.action == 'binaries':
        print(' '.join('--bin ' + entry['binary'] for entry in entries if 'binary' in entry))
        return
    if args.build is None or args.output is None:
        parser.error('install requires --build and --output')
    installer = Path(__file__).with_name('install-if-changed.sh')
    for entry in entries:
        if 'binary' not in entry and 'library' not in entry:
            continue
        destination = args.output / entry['staged']
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['sh', str(installer), entry['mode'],
                        str(args.build / entry.get('binary', entry.get('library'))),
                        str(destination)], check=True)


if __name__ == '__main__':
    main()
