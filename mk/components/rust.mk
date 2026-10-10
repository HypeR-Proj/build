# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Register delivery metadata; Cargo remains the owner of crate dependencies.
# Arguments: source directory, Cargo binary, staged name, install path, profiles.
define native-rust-app
NATIVE_COMPONENT_RECORDS += 'binary|$(1)|$(2)|$(3)|$(4)|$(5)'
endef

# Arguments: source directory, shared-library artifact. All consumers use one
# Cargo invocation, keeping the Rust ABI and feature resolution consistent.
define native-rust-shared
NATIVE_COMPONENT_RECORDS += 'library|$(1)|$(2)|lib/$(2)|lib64/{arch}-hyper-hyper/$(2)|system development io'
endef
