#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Resolve and save the product .config without modifying kernel/.config."""

import argparse
from pathlib import Path
import sys

import system_config
import kconfig
from menuconfig import widgets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('defconfig', 'olddefconfig', 'savedefconfig'))
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    entries = system_config.registry_entries()
    result = kconfig.query('migrate' if args.command == 'olddefconfig' else 'resolve', args.input.resolve())
    product = system_config.resolve(result['values'], entries)
    removed = result['removed']
    product.select(entries)
    content = system_config.format_config(product, entries, minimal=args.command == 'savedefconfig')
    widgets.save_bytes(args.output.resolve(), content.encode())
    for name in removed:
        print(f'product-config: removed obsolete symbol {name}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(f'product-config: {error}')
