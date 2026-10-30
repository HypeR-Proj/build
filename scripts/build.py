#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Prepare an output-owned workspace and invoke its declared build adapters."""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

# Bootstrap runs in the source checkout; leave bytecode out of its directories.
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from buildsystem.model import dependency_order, find_workspace
from buildsystem.prepare import execution


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--module-source', type=Path)
    parser.add_argument('--module', default=os.environ.get('MODULE'))
    parser.add_argument('action', nargs='?', default='all')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    return parser.parse_args()


def main():
    args = arguments()
    root = args.workspace.resolve() if args.workspace else find_workspace(Path.cwd())
    output = (args.out or Path(os.environ.get('OUT', root / 'out'))).resolve()
    arch = os.environ.get('ARCH', 'aarch64')
    toolchain = root / 'mk/workspace/rust-toolchain.toml'
    if not (root / 'rust-toolchain.toml').is_file() and toolchain.is_file():
        os.environ.setdefault('RUSTUP_TOOLCHAIN', tomllib.loads(toolchain.read_text())['toolchain']['channel'])
    with execution(root, output, arch, exclusive='config' in args.action or args.action == 'clean') as (view, modules, changed):
        execute(args, root, output, arch, view, modules, changed)


def execute(args, root, output, arch, view, modules, changed):
    if args.action == 'prepare':
        print(f'Prepared {len(modules)} modules: {output}' if changed else f'Workspace is up to date: {output}')
        return
    if args.action == 'list':
        print(json.dumps({name: {'path': str(m.path), 'build': m.build,
                                'configurable': m.kconfig is not None} for name, m in modules.items()}, indent=2))
        return
    module = modules[args.module] if args.module else None
    if not module and args.module_source and args.module_source.resolve() != modules['build'].source.resolve():
        matches = [m for m in modules.values() if m.source.resolve() == args.module_source.resolve()]
        if len(matches) != 1:
            raise ValueError('module entry point must match one manifest project')
        module = matches[0]
    environment = {**os.environ, 'OUT': str(output), 'HYPER_WORKSPACE': str(root),
                   'HYPER_BUILD_VIEW': '1', 'HYPER_BUILD_OUTPUT': str(output),
                   'HYPER_BUILD_VIEW_PATH': str(view)}
    temporary = output / 'tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    environment['TMPDIR'] = str(temporary)
    pythonpath = [str(view / m.path / path) for m in modules.values()
                  for path in m.metadata.get('workspace', {}).get('pythonpath', [])]
    environment['PYTHONPATH'] = os.pathsep.join([*pythonpath, os.environ.get('PYTHONPATH', '')])
    environment.setdefault('ARCH', arch)
    if 'kernel' in modules:
        environment['HYPER_KERNEL_SOURCE'] = str(modules['kernel'].source)
    environment.setdefault('SYSTEM_CONFIG', str(output / 'config' / arch / '.config')
                           if (output / 'config' / arch / '.config').exists() else '')
    environment.setdefault('SYSTEM_CONFIG_OUTPUT', str(output / 'config' / arch / '.config'))
    for variable, name in (('HYPER_SOURCE_REVISION', 'build'), ('SDK_SOURCE_REVISION', 'toolchain')):
        if name in modules and variable not in environment and (modules[name].source / '.git').exists():
            result = subprocess.run(['git', '-C', str(modules[name].source), 'describe', '--always', '--dirty'],
                                    capture_output=True, text=True)
            environment[variable] = result.stdout.strip() if result.returncode == 0 else 'source-archive'
        environment.setdefault(variable, 'source-archive')
    overrides = []
    for name in os.environ.get('HYPER_MAKE_OVERRIDES', '').split():
        if name not in ('OUT', 'HYPER_BUILD_SYSTEM'):
            overrides.append(name + '=' + os.environ[name])
    for name in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES'):
        environment.pop(name, None)
    if args.action == 'exec':
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            raise ValueError('exec requires a command')
        subprocess.run(command, cwd=view, env=environment, check=True)
        return
    if args.action == 'clean':
        if module:
            raise ValueError('module clean is not supported; run make clean at the workspace root to clear build outputs')
        # Keep configurations, ownership and the lock inode. Removing the lock
        # would allow another process to prepare a view while cleanup is active.
        for name in ('build', 'sdk', 'workspace', 'staging', 'fixtures', 'results', 'tmp', 'cache'):
            path = output / name
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        return
    if module:
        if args.action in ('menuconfig', 'config'):
            from buildsystem.config import menu
            menu(view, output, replace(module, source=view / module.path), arch)
            return
        if args.action == 'test':
            commands = module.metadata.get('tests', [])
            if not commands:
                raise ValueError(f'{module.name}: no module tests are declared')
            for command in commands:
                subprocess.run([value.replace('{component}', str(view / module.path))
                                .replace('{system}', str(view)) for value in command],
                               cwd=view, env=environment, check=True)
            return
        actions = module.build.get('actions', {})
        if actions and args.action not in actions and '*' not in actions:
            raise ValueError(f'{module.name}: unsupported build action {args.action}')
        names = dependency_order(modules, [module.name])
        for name in names:
            current = modules[name]
            recipe = current.build
            if not recipe:
                continue
            variables = {'workspace': str(view), 'source': str(view / current.path),
                         'out': str(output / 'build' / name), 'output': str(output),
                         'arch': arch, 'action': args.action if name == module.name else 'all',
                         'module': name, 'sdk': environment.get('SDK_OUTPUT', str(output / 'sdk' / arch)),
                         'config': str(output / 'config' / arch / 'modules' / (name + '.config')),
                         'config_header': str(output / 'config' / arch / 'generated' / name / 'config.h')}
            configured = output / 'config' / arch / 'generated' / name / 'config.json'
            local = {**environment, **(json.loads(configured.read_bytes()) if configured.exists() else {})}
            local.update({key: value.format_map(variables) for key, value in recipe.get('environment', {}).items()})
            actions = recipe.get('actions', {})
            steps = actions.get(variables['action'], actions.get('*')) if actions else recipe.get('steps', [])
            if steps is None:
                raise ValueError(f'{name}: unsupported build action {variables["action"]}')
            for step in steps:
                if not isinstance(step, list) or not step or any(not isinstance(v, str) for v in step):
                    raise ValueError(f'{name}: build steps must be argument arrays')
                command = [value.format_map(variables) for value in step]
                if Path(command[0]).name == 'make':
                    command.extend(overrides)
                subprocess.run(command, cwd=view, env=local, check=True)
        return
    command = ['make', '--no-print-directory', args.action, *overrides]
    subprocess.run(command, cwd=view, env=environment, check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'build: {error}', file=sys.stderr)
        sys.exit(1)
