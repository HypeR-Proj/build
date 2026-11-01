<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Editor checks

The build repository owns `.vscode` and `.clang-format`; manifest `linkfile`
entries expose them at the source workspace root. Existing formatting and
check policy remains in those files. Architecture-specific Cargo project and
SDK paths are derived into OUT, without writing generated settings to a repository.

Run `make prepare OUT="$PWD/out"` and `make sdk`, then open
`out/HypeR-aarch64.code-workspace` in VS Code with Rust Analyzer installed.
Use `ARCH=riscv64` for the corresponding generated editor workspace. Edit source
repositories; preparation owns the copied build controls and editor settings.

Linked Cargo projects are generated from repository-owned workspace roots.
Applications, services and libraries retain independent lockfiles. A library's
`shared/` crate belongs to its library repository; coreutils and system-tests
each own their local multi-package workspace. Adding a registered module does
not require editing a fixed Rust Analyzer project list.

Native checks use the installed `hyper-cargo` driver and the module's declared
features. Bootstrap uses static linkage. Kernel and host tools retain their
ordinary Cargo configuration. Diagnostics and build-script checks share the
same repository-aware entry point. Their cache is separate from production
artifacts under `OUT/build/rust-analyzer/ARCH`.

Run preparation again after changing module registration, then reload the VS
Code window if it retains old settings. Changing a Rust source file does not
require regenerating the editor workspace.
