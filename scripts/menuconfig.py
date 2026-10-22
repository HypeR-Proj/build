#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Edit product deployment through the existing component registry and schema."""

import argparse
import copy
import curses
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from native_components import load_registry
import system_config
import kconfig

ROOT = Path(__file__).resolve().parents[1]
# Reuse terminal widgets only. Kernel configuration remains independently
# runnable and has no import or dependency on product composition.
spec = importlib.util.spec_from_file_location(
    'kernel_menu', ROOT / 'kernel/tools/kconfig/menuconfig.py')
widgets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(widgets)


class Configuration:
    def __init__(self, output, entries):
        self.output = output.resolve()
        self.entries = entries
        self.saved = self.output.read_bytes() if self.output.exists() else None
        product = system_config.load(self.output, entries) if self.saved is not None else system_config.ProductConfig({'version': 1})
        self.value = product.value
        self.policy_path = product.policy_path
        self.initial = copy.deepcopy(self.value)
        self.initial_policy = self.policy_path
        self.refresh()

    def refresh(self):
        product = system_config.ProductConfig(self.value)
        self.selected = {scope: product.selected_names(self.entries, scope)
                         for scope in ('boot', 'system')}
        self.symbols = system_config.component_symbols(self.entries)
        entries = [{
            'name': 'kernel', 'kind': 'action', 'value': '', 'range': None,
            'section': 'Kernel', 'prompt': 'Open kernel menuconfig',
            'help': 'Kernel settings save CONFIG_FILE independently. Discarding product edits '
                    'does not undo a saved kernel configuration.',
        }]
        programs = {(entry['environment'], entry['staged']): entry
                    for entry in self.entries if 'binary' in entry}
        for name, definition in kconfig.schema().items():
            entry = {**definition, 'range': None}
            if name in self.symbols:
                scope, program = self.symbols[name]
                declaration = programs[scope, program]
                entry['value'] = 'y' if program in self.selected[scope] else 'n'
                dependencies = ', '.join(declaration.get('runtime_requires', [])) or 'none'
                entry['help'] += ('\nRequired programs: ' + dependencies +
                                  '\nLibraries follow Cargo and ELF dependencies. '
                                  'Filesystem selection does not change fstab or disk formats.')
            elif name == 'CONFIG_KERNEL_FEATURES':
                entry['value'] = ','.join(self.value['kernel']['features']) if 'kernel' in self.value else 'default'
            elif name == 'CONFIG_BOOT_POLICY':
                entry['value'] = self.policy_path
            else:
                raise ValueError(f'unhandled product setting {name}')
            entries.append(entry)
        self.state = {'title': 'HypeR System Configuration', 'entries': entries, 'removed': []}

    def update(self, name, value):
        candidate = copy.deepcopy(self.value)
        policy_path = self.policy_path
        if name in ('CONFIG_BOOT_POLICY', 'CONFIG_KERNEL_FEATURES'):
            # Re-evaluate policy using the same typed values as the Kconfig
            # backend. Paths are data, never shell or Makefile expressions.
            values = {entry['name']: entry['value'] for entry in self.state['entries']
                      if entry['kind'] != 'action'}
            values[name] = value
            product = system_config.resolve(values, self.entries)
            candidate, policy_path = product.value, product.policy_path
        else:
            scope, component = self.symbols[name]
            section = candidate[scope]
            include, exclude = set(section['include']), set(section['exclude'])
            include.discard(component)
            exclude.discard(component)
            (include if value == 'y' else exclude).add(component)
            section.update(include=sorted(include), exclude=sorted(exclude))
        product = system_config.ProductConfig(candidate)
        product.policy_path = policy_path
        with tempfile.NamedTemporaryFile() as working:
            working.write(system_config.format_config(product, self.entries).encode())
            working.flush()
            # Disabling a prerequisite also disables its dependent programs.
            # Enabling a program with absent prerequisites is an explicit error.
            product = system_config.load(Path(working.name), self.entries,
                                         migrate=name in self.symbols and value == 'n')
        self.value, self.policy_path = product.value, product.policy_path
        self.refresh()

    def dirty(self):
        return self.saved is None or self.value != self.initial or self.policy_path != self.initial_policy

    def save(self):
        # Validate the same complete deployment graph used by packaging.
        product = system_config.ProductConfig(self.value)
        product.select(self.entries)
        product.policy_path = self.policy_path
        content = system_config.format_config(product, self.entries).encode()
        # Validate the serialized file with the same parser as a normal build,
        # before touching the user's file. This also re-reads runtime policy.
        with tempfile.NamedTemporaryFile() as working:
            working.write(content)
            working.flush()
            checked = system_config.load(Path(working.name), self.entries)
            checked.select(self.entries)
        widgets.save_bytes(self.output, content)
        self.saved = content
        self.initial = copy.deepcopy(self.value)
        self.initial_policy = self.policy_path


class ProductMenu(widgets.Menu):
    def __init__(self, screen, config, kernel_config, arch):
        super().__init__(screen, config)
        self.kernel_config = kernel_config
        self.arch = arch
        self.notice = 'S saves product selection. Kernel settings have their own save.'

    @staticmethod
    def label(entry):
        if entry['kind'] == 'action':
            return entry['prompt'] + ' --->'
        return widgets.Menu.label(entry)

    @staticmethod
    def detail(entry):
        return entry['name']

    def edit(self, entry, key):
        if entry['kind'] != 'action':
            return super().edit(entry, key)
        if key not in widgets.ENTER + (' ',):
            return
        # The child owns the terminal until it exits. It is exactly the same
        # entry point as `make -C kernel menuconfig`, including save semantics.
        curses.def_prog_mode()
        curses.endwin()
        try:
            environment = dict(os.environ)
            for name in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES'):
                environment.pop(name, None)
            result = subprocess.run(
                ['make', '--no-print-directory', '-C', str(ROOT / 'kernel'), 'menuconfig',
                 'ARCH=' + self.arch, 'CONFIG_FILE=' + str(self.kernel_config)],
                env=environment, check=False)
        finally:
            curses.reset_prog_mode()
            curses.curs_set(0)
            self.screen.clear()
            self.screen.refresh()
        self.notice = ('Returned from kernel menu; kernel saves are independent.'
                       if result.returncode == 0 else
                       f'Kernel menu exited with status {result.returncode}; product edits retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.config')
    parser.add_argument('--kernel-config', type=Path, default=ROOT / 'kernel/.config')
    parser.add_argument('--arch', choices=('aarch64', 'riscv64', 'x86_64'), default='aarch64')
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('TERM') in (None, '', 'dumb'):
        parser.error('menuconfig needs an interactive terminal with TERM set')
    entries = load_registry(ROOT / 'mk/components.mk')['entries']
    config = Configuration(args.output, entries)
    curses.wrapper(lambda screen: ProductMenu(screen, config, args.kernel_config.resolve(), args.arch).run())


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit('menuconfig: unsaved product changes discarded')
    except (OSError, ValueError, subprocess.CalledProcessError, curses.error) as error:
        sys.exit(f'menuconfig: {error}')
