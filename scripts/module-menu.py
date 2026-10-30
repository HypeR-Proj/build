#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Open one module's menu within an already prepared workspace."""

import argparse
import os
from pathlib import Path

from buildsystem.config import menu
from buildsystem.model import discover

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('module')
parser.add_argument('--arch', default='aarch64')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
menu(root, Path(os.environ['HYPER_BUILD_OUTPUT']), discover(root)[args.module], args.arch)
