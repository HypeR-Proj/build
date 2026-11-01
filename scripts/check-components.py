#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate source catalogs without generating a workspace or building an SDK."""

import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from buildsystem.model import dependency_order, discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--module')
    args = parser.parse_args()
    modules = discover(args.workspace.resolve())
    dependency_order(modules, modules)
    if args.module and args.module not in modules:
        raise ValueError(f'unknown component: {args.module}')
    print(f'Validated {len(modules)} components and their dependency graph')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(f'components: {error}')
