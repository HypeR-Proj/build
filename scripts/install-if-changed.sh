#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Retain downstream timestamps when Cargo reuses an existing build product.
set -eu
if [ "$#" -ne 3 ]; then
    echo "usage: install-if-changed.sh MODE SOURCE DESTINATION" >&2
    exit 2
fi
if cmp -s "$2" "$3"; then
    chmod "$1" "$3"
else
    install -m "$1" "$2" "$3"
fi
