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
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        board = Board.load(args.board, recovery=args.recovery)
        contents = json.dumps(board.source, indent=2) + '\n'
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not args.output.exists() or args.output.read_text() != contents:
            args.output.write_text(contents)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Native deployment: {error}\n')


if __name__ == '__main__':
    main()
