#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Select editor checks by repository ownership and the installed Native SDK."""

import os
import json
from pathlib import Path
import subprocess
import sys
import tomllib

from buildsystem.model import discover, find_workspace
from buildsystem.toolchain import native_environment


def main():
    root = Path(__file__).resolve().parents[1]
    current = Path.cwd().resolve()
    description = root / 'component.json'
    if description.is_file() and json.loads(description.read_text()).get('name') == 'build':
        # Source-folder checks enter the same prepared view as Make. Cargo
        # metadata and compiler diagnostics then use the installed SDK paths.
        source = find_workspace(root)
        directory = current.relative_to(source) if current.is_relative_to(source) else Path('kernel')
        command = [sys.executable, '-B', str(root / 'scripts/build.py'), 'exec', '--',
                   sys.executable, '-B', 'scripts/rust-analyzer-check.py', str(directory)]
        os.execv(sys.executable, command)
    if len(sys.argv) > 1:
        current = root / sys.argv[1]
    if current == root:
        current = root / 'kernel'
    manifest = Path(subprocess.check_output(['cargo', 'locate-project', '--workspace', '--message-format', 'plain'],
                                           cwd=current, text=True).strip())
    directory = manifest.parent.resolve()
    modules = discover(root)
    owners = [module for module in modules.values() if directory.is_relative_to(module.source)]
    owner = max(owners, key=lambda module: len(module.source.parts)) if owners else None
    entries = owner.metadata.get('cargo', []) if owner else []
    configuration = directory / '.cargo/config.toml'
    configuration = tomllib.loads(configuration.read_text()) if configuration.is_file() else {}
    native = 'hyper-os' in configuration.get('patch', {}).get('crates-io', {})
    if owner and directory != owner.source:
        entries = [entry for entry in entries if (owner.source / entry['manifest']).parent == directory]
    arch = os.environ.get('ARCH', 'aarch64')
    output = Path(os.environ['HYPER_BUILD_OUTPUT'])
    environment = native_environment(root)
    if entries or native:
        sdk = Path(os.environ.get('SDK_OUTPUT', output / 'sdk' / arch))
        driver = sdk / 'bin/hyper-cargo'
        if not driver.is_file():
            raise ValueError(f'run make sdk ARCH={arch} to prepare Native editor checks')
        environment.update(HYPER_SYSROOT=str(sdk), HYPER_ARCH=arch,
                           HYPER_RUST_STD=configuration.get('env', {}).get('HYPER_RUST_STD', '1'),
                           HYPER_LINK_MODE=configuration.get('env', {}).get('HYPER_LINK_MODE', 'dynamic'))
        command = [str(driver), 'check', '--workspace']
        for entry in entries:
            if entry.get('shared'):
                package = tomllib.loads((owner.source / entry['manifest']).read_text())['package']['name']
                if not entry['default_features']:
                    command.append('--no-default-features')
                if entry['features']:
                    command += ['--features', ','.join(package + '/' + feature for feature in entry['features'])]
        cache = 'native'
    else:
        environment.setdefault('HYPER_CONFIG', str(output / 'config' / arch / 'modules/kernel.config'))
        command = ['cargo', 'check', '--workspace', '--all-targets']
        cache = 'host'
    command += ['--manifest-path', str(manifest), '--locked', '--message-format=json',
                '--target-dir', str(output / 'build/rust-analyzer' / arch / cache)]
    os.chdir(directory)
    os.execvpe(command[0], command, environment)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'rust-analyzer: {error}')
