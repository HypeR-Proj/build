# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Resolve each module's Kconfig in a separate parser context and output file."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

from . import files
from .model import relative


def backend(root, output):
    source = root / 'kernel/tools/kconfig'
    version = subprocess.check_output(['rustc', '-vV'], cwd=root, text=True)
    host = next(line[6:] for line in version.splitlines() if line.startswith('host: '))
    target = output / 'build/host/kconfig'
    executable = target / host / 'debug/hyper-kconfig'
    fingerprint = hashlib.sha256(version.encode())
    for path in sorted([*source.rglob('*.rs'), source / 'Cargo.toml', source / 'Cargo.lock']):
        fingerprint.update(str(path.relative_to(source)).encode())
        fingerprint.update(path.read_bytes())
    stamp = target / 'inputs.sha256'
    identity = fingerprint.hexdigest()
    if not executable.is_file() or not stamp.exists() or stamp.read_text() != identity:
        subprocess.run(['cargo', 'build', '--quiet', '--locked', '--offline', '--manifest-path',
                        str(source / 'Cargo.toml'), '--target', host, '--target-dir', str(target)],
                       cwd=root, check=True)
        files.write(stamp, identity)
    return executable


def query(executable, schema, command, *arguments):
    result = subprocess.run([str(executable), '--schema', str(schema), command,
                             *map(str, arguments)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def format_config(schema, values):
    result = ['# Generated defaults and saved module settings. Local CONFIG_ names belong only to this module.']
    for entry in schema['entries']:
        if entry['name'] not in values:
            continue
        name, value = entry['name'], values[entry['name']]
        if entry['kind'] == 'string':
            value = json.dumps(value, ensure_ascii=False)
        result.append(f'# {name} is not set' if value == 'n' else f'{name}={value}')
    return '\n'.join(result) + '\n'


def prepare_configs(root, output, modules, arch):
    configured = [m for m in modules.values() if m.kconfig is not None]
    if not configured:
        return {}
    executable = backend(root, output)
    result = {}
    directory = output / 'config' / arch / 'modules'
    directory.mkdir(parents=True, exist_ok=True)
    for module in configured:
        schema = query(executable, module.kconfig, 'describe')
        path = directory / (module.name + '.config')
        if path.exists():
            source = path
        elif defaults := module.configuration.get('defaults'):
            source = module.source / relative(defaults.format(arch=arch))
        else:
            source = directory / '.empty.config'
            files.write(source, '')
        resolved = query(executable, module.kconfig, 'migrate', source)
        # Resolve before publication. Invalid edits never replace a saved file.
        files.write(path, format_config(schema, resolved['values']))
        values = resolved['values']
        result[module.name] = values
        generated = output / 'config' / arch / 'generated' / module.name
        files.write(generated / 'config.json', json.dumps(values, sort_keys=True, indent=2) + '\n')
        header = ['/* Generated module-local configuration. */', '#pragma once']
        for entry in schema['entries']:
            if entry['name'] not in values:
                continue
            value = values[entry['name']]
            if entry['kind'] == 'bool':
                if value != 'y':
                    continue
                value = '1'
            elif entry['kind'] == 'string':
                value = json.dumps(value)
            header.append(f'#define {entry["name"]} {value}')
        files.write(generated / 'config.h', '\n'.join(header) + '\n')
    return result


def prepare_product(root, output, arch, content):
    directory = output / 'config' / arch
    schema = directory / 'Kconfig'
    files.write(schema, content)
    path = directory / '.config'
    empty = directory / '.empty.config'
    files.write(empty, '')
    executable = backend(root, output)
    values = query(executable, schema, 'migrate', path if path.exists() else empty)['values']
    files.write(path, format_config(query(executable, schema, 'describe'), values))
    return path


def menu(root, output, module, arch):
    if module.kconfig is None:
        raise ValueError(f'{module.name}: this module has no configurable options')
    path = output / 'config' / arch / 'modules' / (module.name + '.config')
    if command := module.configuration.get('menu'):
        values = {'source': str(module.source), 'config': str(path), 'arch': arch}
        subprocess.run([value.format_map(values) for value in command], cwd=root, check=True)
        return
    import curses
    source = root / 'kernel/tools/kconfig/menuconfig.py'
    spec = importlib.util.spec_from_file_location('module_config_widgets', source)
    widgets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(widgets)
    executable = backend(root, output)

    class Configuration(widgets.Configuration):
        def update(self, name=None, value=None):
            arguments = [self.working]
            if name is not None:
                arguments.extend((name, value))
            self.state = query(executable, module.kconfig, 'menuconfig-state', *arguments)
            self.state['title'] = module.name + ' — ' + self.state['title']
            return self.state

    with tempfile.TemporaryDirectory(prefix='.menu-', dir=path.parent) as temporary:
        config = Configuration(str(executable), path, path, Path(temporary) / 'working.config')
        curses.wrapper(lambda screen: widgets.Menu(screen, config).run())
