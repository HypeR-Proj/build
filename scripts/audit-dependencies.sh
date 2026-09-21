#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Lockfile auditing includes installed-SDK consumers without resolving their
# unpublished crates or building a Native sysroot. Audit every graph on failure.
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
git ls-files '*Cargo.lock' | {
    failed=0
    while IFS= read -r lockfile; do
        echo "Auditing $lockfile"
        cargo audit --file "$lockfile" || failed=1
    done
    exit "$failed"
}
