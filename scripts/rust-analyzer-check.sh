#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# rust-analyzer runs this from each linked Cargo workspace. Use the same Native
# driver as production, while checking kernel/host projects on the host target.
set -eu
script_directory=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd -- "$script_directory/.." && pwd)
workspace=$(pwd -P)
config=$root/.vscode/rust-analyzer.toml

case "$workspace" in
    "$root/app" | "$root/sdk/toolchain/tests/std-smoke" | "$root/sdk/toolchain/tests/rust-smoke")
        sdk=$root/target/sdk/aarch64
        if [ ! -x "$sdk/bin/hyper-cargo" ]; then
            echo "rust-analyzer: run make sdk ARCH=aarch64 to prepare Native std" >&2
            exit 2
        fi
        export HYPER_SYSROOT="$sdk" HYPER_ARCH=aarch64 HYPER_RUST_STD=1
        if [ "$workspace" = "$root/sdk/toolchain/tests/rust-smoke" ]; then
            HYPER_RUST_STD=0
        fi
        # Keep navigation on repository SDK sources, not the installed copies.
        # Native executables have no host test harness.
        exec "$sdk/bin/hyper-cargo" check --workspace --message-format=json \
            --config "$config" --target-dir "$root/target/rust-analyzer/native"
        ;;
    *)
        exec cargo check --workspace --all-targets --message-format=json \
            --config "$config" --target-dir "$root/target/rust-analyzer/host"
        ;;
esac
