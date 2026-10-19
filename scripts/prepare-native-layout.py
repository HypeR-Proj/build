#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Materialize the selected board layout without modifying its source policy."""

import argparse
import json
from pathlib import Path

from board_config import Board


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', type=Path, required=True)
    parser.add_argument('--recovery', action='store_true',
                        help='prepare a stage1-only hardware recovery disk without formal system')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--artifacts', action='store_true',
                        help='print the artifact names required by this board without writing files')
    args = parser.parse_args()
    if args.artifacts == (args.output is not None):
        parser.error('select exactly one of --output or --artifacts')
    try:
        board = Board.load(args.board, recovery=args.recovery)
        if args.artifacts:
            names = set(board.source['files'].values())
            names.update(part.image for part in board.partitions if part.image)
            print(' '.join(sorted(names)))
            return
        contents = json.dumps(board.source, indent=2) + '\n'
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not args.output.exists() or args.output.read_text() != contents:
            args.output.write_text(contents)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Native deployment: {error}\n')


if __name__ == '__main__':
    main()
