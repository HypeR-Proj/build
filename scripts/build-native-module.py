#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Build one repository's Native Cargo packages using the shared SDK/cache."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from buildsystem.model import discover, relative
from buildsystem.toolchain import native_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--action', choices=('all', 'build', 'check'), default='all')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    modules = discover(root)
    module = modules[args.module]
    output = Path(os.environ['HYPER_BUILD_OUTPUT'])
    arch = os.environ.get('ARCH', 'aarch64')
    sdk = Path(os.environ.get('SDK_OUTPUT', output / 'sdk' / arch))
    policies = modules['build'].metadata['workspace']['cargo']
    artifacts = module.metadata.get('artifacts', [])
    groups = {}
    for entry in module.metadata.get('cargo', []):
        manifest = relative(entry['manifest'])
        runtime = next((a.get('rust_runtime', 'std') for a in artifacts
                        if a.get('manifest') == entry['manifest']), 'std')
        package = tomllib.loads((module.source / manifest).read_text())['package']['name']
        groups.setdefault((entry['domain'], runtime), []).append(package)
    logs, caches = [], {}
    for (domain, runtime), packages in sorted(groups.items()):
        cache = output / 'build/native' / arch / domain
        caches[domain] = cache
        log = output / 'build' / module.name / f'artifacts-{domain}-{runtime}.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        environment = {**native_environment(root), 'CARGO_TARGET_DIR': str(cache), 'HYPER_SYSROOT': str(sdk),
                       'HYPER_ARCH': arch, 'HYPER_LINK_MODE': 'static' if domain == 'boot' else 'dynamic',
                       'HYPER_RUST_STD': '0' if runtime == 'core' else '1'}
        command = [str(sdk / 'bin/hyper-cargo'), 'check' if args.action == 'check' else 'build',
                   '--manifest-path', str(root / policies[domain]['directory'] / 'Cargo.toml'),
                   '--release', '--locked', '--offline', '--message-format=json-render-diagnostics']
        command.extend(value for package in packages for value in ('--package', package))
        with log.open('w') as stream:
            subprocess.run(command, cwd=root, env=environment, stdout=stream, check=True)
        logs.append(log)
    if args.action == 'check':
        return
    for domain, cache in sorted(caches.items()):
        selected = [a['staged'] for a in artifacts if 'binary' in a
                    and a.get('environment', 'system') == domain]
        if not selected:
            continue
        native = cache / f'{arch}-unknown-hyper/release'
        bare = cache / {'aarch64': 'aarch64-unknown-none',
                        'riscv64': 'riscv64gc-unknown-none-elf'}[arch] / 'release'
        command = [sys.executable, '-B', str(root / 'scripts/app-deployment.py'), 'install',
                   '--manifest', str(root / 'mk/components.mk'), '--profile', 'development',
                   '--link-mode', 'static' if domain == 'boot' else 'dynamic',
                   '--build', str(native), '--static-build', str(native),
                   '--core-build', str(bare), '--static-core-build', str(bare), '--sdk', str(sdk),
                   '--output', str(output / 'staging/modules' / module.name / domain)]
        command.extend(value for name in selected for value in ('--component', name))
        command.extend(value for log in logs for value in ('--cargo-artifacts', str(log)))
        subprocess.run(command, cwd=root, check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'native module: {error}')
