#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Emit the Native delivery adapter's records from repository metadata."""

from pathlib import Path
import sys

from buildsystem.composition import records
from buildsystem.model import discover

try:
    print('\n'.join(records(discover(Path(__file__).resolve().parents[1]))))
except (OSError, ValueError, KeyError) as error:
    sys.exit(f'component records: {error}')
