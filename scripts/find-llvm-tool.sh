#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Resolve an LLVM utility without assuming that Homebrew's LLVM is on PATH.
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: find-llvm-tool.sh TOOL" >&2
    exit 2
fi

tool=$1
if command -v "$tool" >/dev/null 2>&1; then
    command -v "$tool"
    exit 0
fi

for prefix in /opt/homebrew/opt/llvm /usr/local/opt/llvm; do
    candidate=$prefix/bin/$tool
    if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
        exit 0
    fi
done

if [ "$tool" = ld.lld ] && command -v rustc >/dev/null 2>&1; then
    rust_sysroot=$(rustc --print sysroot)
    rust_host=$(rustc -vV | sed -n 's/^host: //p')
    candidate=$rust_sysroot/lib/rustlib/$rust_host/bin/gcc-ld/ld.lld
    if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
        exit 0
    fi
fi

echo "find-llvm-tool.sh: required LLVM tool is unavailable: $tool" >&2
exit 2
