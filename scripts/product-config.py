#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Resolve product configuration and optionally prepare matching kernel defaults."""

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import system_config
import kconfig
from menuconfig import widgets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('defconfig', 'olddefconfig', 'savedefconfig'))
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kernel-input', type=Path)
    parser.add_argument('--kernel-output', type=Path)
    args = parser.parse_args()
    if bool(args.kernel_input) != bool(args.kernel_output):
        parser.error('--kernel-input and --kernel-output must be supplied together')
    if args.kernel_input is not None and args.command != 'defconfig':
        parser.error('kernel defaults can only be reset with defconfig')
    output = args.output.resolve()
    kernel_output = args.kernel_output.resolve() if args.kernel_output is not None else None
    if output == kernel_output:
        parser.error('product and kernel configurations must select different files')
    entries = system_config.registry_entries()
    result = kconfig.query('migrate' if args.command == 'olddefconfig' else 'resolve', args.input.resolve())
    product = system_config.resolve(result['values'], entries)
    removed = result['removed']
    product.select(entries)
    content = system_config.format_config(product, entries, minimal=args.command == 'savedefconfig')
    configurations = [(output, content.encode())]
    if kernel_output is not None:
        # Use the kernel's authoring CLI, including its architecture and budget
        # constraints. A generic product-schema resolution is insufficient.
        with tempfile.TemporaryDirectory(prefix='hyper-kernel-defconfig-') as directory:
            staged = Path(directory) / 'kernel.config'
            subprocess.run([str(kconfig.backend()), 'defconfig', str(args.kernel_input.resolve()),
                            str(staged)], cwd=kconfig.ROOT / 'kernel', check=True,
                           stdout=subprocess.DEVNULL)
            configurations.append((kernel_output, staged.read_bytes()))
    # Finish validation and prepare every destination before changing either
    # existing file, including failure to create a sibling in an output folder.
    # Each rename is atomic; this is not a power-loss transaction across files.
    with ExitStack() as stack:
        staged = [(destination, stack.enter_context(widgets.staged_bytes(destination, data)))
                  for destination, data in configurations]
        for destination, temporary in staged:
            os.replace(temporary, destination)
    for name in removed:
        print(f'product-config: removed obsolete symbol {name}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'product-config: {error}')
