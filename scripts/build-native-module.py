#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Build repository-owned Native workspaces and validate shared-library contracts."""

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from buildsystem.model import discover
from buildsystem.native import Builder
from buildsystem.link_context import source_context
from native_components import cargo_targets, extra_binary
import system_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module')
    parser.add_argument('--action', choices=('all', 'build', 'check', 'fetch'), default='build')
    parser.add_argument('--profile', choices=('system', 'development', 'io'), default='system')
    parser.add_argument('--system-config', type=Path)
    parser.add_argument('--features', default='')
    parser.add_argument('--extra-bin', action='append', default=[])
    parser.add_argument('--system-cache', type=Path)
    parser.add_argument('--boot-cache', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    modules = discover(root)
    output = Path(os.environ['HYPER_BUILD_OUTPUT'])
    arch = os.environ.get('ARCH', 'aarch64')
    sdk = Path(os.environ.get('SDK_OUTPUT', output / 'sdk' / arch))
    caches = {domain: getattr(args, domain + '_cache') or output / 'build/native' / arch / domain
              for domain in ('boot', 'system')}
    builder = Builder(root, modules, output, arch, sdk, caches)
    if args.action == 'fetch':
        commands = []
        for module in modules.values():
            entries = module.metadata.get('cargo', [])
            if not entries:
                continue
            for entry in entries:
                workspace = module.source / 'Cargo.toml'
                manifest = module.source / entry['manifest']
                name = tomllib.loads(manifest.read_text())['package']['name']
                locked = ['--locked']
                if entry['domain'] == 'system':
                    if entry.get('shared'):
                        manifest = workspace = module.source / entry['link_probe']
                        name = 'provider-' + name
                    manifest = source_context(manifest, workspace, caches['system'] / 'sources' / name,
                                              builder.cache.controls)
                    locked = []
                else:
                    manifest = workspace
                command = [str(sdk / 'bin/hyper-cargo'), 'fetch', *locked, '--manifest-path', str(manifest)]
                if command not in commands:
                    commands.append(command)
        environment = builder.environment('system')
        builder.cache.fetch(commands, environment,
                            lambda command: subprocess.run(command, cwd=root, env=environment, check=True))
        return
    action = 'check' if args.action == 'check' else 'build'
    features = args.features.replace(',', ' ').split()
    selected, binaries = {}, {}
    if args.module:
        def select(name):
            module = modules[name]
            if module.build.get('type') == 'group':
                for include in module.metadata.get('include', []):
                    select(include['project'])
            else:
                entries = module.metadata.get('cargo', [])
                selected[name] = [entry for entry in entries if entry.get('shared')] or entries
        select(args.module)
    else:
        spec = importlib.util.spec_from_file_location('app_deployment', root / 'scripts/app-deployment.py')
        deployment = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deployment)
        entries = deployment.load(root / 'mk/components.mk')
        product = system_config.load(args.system_config, entries) if args.system_config else system_config.ProductConfig({'version': 1})
        deployed = product.select(entries, args.profile)
        cargo_targets(entries, deployed, None, args.features, args.extra_bin, executable_roots=True)
        wanted = {entry['package'] for entry in deployed if 'binary' in entry}
        for entry in deployed:
            if 'binary' in entry:
                binaries.setdefault(entry['package'], set()).add(entry['binary'])
        for binary in args.extra_bin:
            owner = extra_binary(entries, binary)
            wanted.add(owner['package'])
            binaries.setdefault(owner['package'], set()).add(binary)
        for module in modules.values():
            for entry in module.metadata.get('cargo', []):
                name = tomllib.loads((module.source / entry['manifest']).read_text())['package']['name']
                if name in wanted:
                    selected.setdefault(module.name, []).append(entry)
    for name, entries in selected.items():
        module = modules[name]
        if not args.module:
            for entry in entries:
                package = tomllib.loads((module.source / entry['manifest']).read_text())['package']['name']
                builder.build(module, [entry], action, sorted(binaries[package]), features)
        else:
            builder.build(module, entries, action, features=features)
    if not args.module and action == 'build':
        builder.publish_records()
    elif action == 'build':
        for name in selected:
            for domain, cache in caches.items():
                artifacts = [item['staged'] for item in modules[name].metadata.get('artifacts', [])
                             if 'binary' in item and item.get('environment', 'system') == domain]
                if not artifacts:
                    continue
                native = cache / f'{arch}-unknown-hyper/release'
                target = {'aarch64': 'aarch64-unknown-none', 'riscv64': 'riscv64gc-unknown-none-elf'}[arch]
                core = cache / target / 'release'
                command = [sys.executable, '-B', str(root / 'scripts/app-deployment.py'), 'install',
                           '--manifest', str(root / 'mk/components.mk'), '--profile', 'development',
                           '--link-mode', 'static' if domain == 'boot' else 'dynamic',
                           '--build', str(native), '--static-build', str(native),
                           '--core-build', str(core), '--static-core-build', str(core), '--sdk', str(sdk),
                           '--output', str(output / 'staging/modules' / name / domain)]
                command += [value for item in artifacts for value in ('--component', item)]
                command += [value for d, _, log in builder.records if d == domain
                            for value in ('--cargo-artifacts', str(log))]
                subprocess.run(command, cwd=root, check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'native module: {error}')
