#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

set -eu
script_directory=$(CDPATH='' cd -- "$(dirname "$0")" && pwd -P)
exec python3 -B "$script_directory/rust-analyzer-check.py"
