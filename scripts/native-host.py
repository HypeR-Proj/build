#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Run Native repositories' formatting, host tests, or SDK compile checks."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from buildsystem.model import discover
from buildsystem.native import Builder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('test', 'fmt', 'check'), required=True)
    parser.add_argument('--package')
    parser.add_argument('--target')
    parser.add_argument('--abi', type=Path)
    parser.add_argument('--rust', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    modules = discover(root)
    output = Path(os.environ['HYPER_BUILD_OUTPUT'])
    arch = os.environ.get('ARCH', 'aarch64')
    sdk = Path(os.environ.get('SDK_OUTPUT', output / 'sdk' / arch))
    builder = Builder(root, modules, output, arch, sdk)
    selected = []
    for module in modules.values():
        entries = []
        for entry in module.metadata.get('cargo', []):
            name = tomllib.loads((module.source / entry['manifest']).read_text())['package']['name']
            if args.package is None or args.package == name:
                entries.append((entry, name))
        if entries:
            selected.append((module, entries))
    if args.package and sum(len(entries) for _, entries in selected) != 1:
        raise ValueError(f'package must identify one Native repository target: {args.package}')
    trailing = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    for module, entries in selected:
        manifest = module.source / 'Cargo.toml'
        if args.action == 'check':
            builder.build(module, [entry for entry, _ in entries], 'check')
            continue
        if args.action == 'fmt':
            subprocess.run(['cargo', 'fmt', '--manifest-path', str(manifest), '--all', '--', '--check'], check=True)
            continue
        packages = [name for entry, name in entries if not entry.get('shared')]
        if not packages:
            continue
        if not args.abi or not args.rust or not args.target:
            raise ValueError('host tests require --abi, --rust and --target')
        command = ['cargo', 'test', '--manifest-path', str(manifest), '--target', args.target, '--locked']
        command += [value for name in packages for value in ('--package', name)]
        for name, path in [('hyper-abi', args.abi), *[(name, args.rust / name) for name in
                           ('hyper-os', 'hyper-filesystem', 'hyper-rt', 'hyper-sys')]]:
            command += ['--config', f'patch.crates-io.{name}.path={str(path)!r}']
        subprocess.run([*command, *trailing], cwd=root,
                       env={**os.environ, 'CARGO_TARGET_DIR': str(output / 'build/native-host' / module.name)}, check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'native host: {error}')
