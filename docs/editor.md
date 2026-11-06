<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Editor checks

The build repository owns `.vscode` and `.clang-format`; manifest `linkfile`
entries expose them at the source workspace root. Existing formatting and
check policy remains in those files. Architecture-specific Cargo project and
SDK paths are derived into OUT, without writing generated settings to a repository.

Run `make prepare OUT="$PWD/out"` and `make sdk`, then open the HypeR source
workspace root in VS Code with Rust Analyzer installed. Preparation generates
settings under OUT and publishes an ignored settings symlink in the build-owned
`.vscode` directory. The manifest still owns the root directory link; generated
settings never become tracked source files. Existing Rust/C/C++ formatting is
preserved. Re-run `make prepare` to repair the link or select another OUT/ARCH.

`out/HypeR-aarch64.code-workspace` remains an alternate entry. Use `ARCH=riscv64`
for the corresponding generated editor workspace. Edit source repositories;
preparation owns the copied build controls and editor settings.

Linked Cargo projects are generated from repository-owned workspace roots.
Applications, services and libraries retain independent lockfiles. A library's
`shared/` crate belongs to its library repository; coreutils and system-tests
each own their local multi-package workspace. Adding a registered module does
not require editing a fixed Rust Analyzer project list. Prepared Cargo manifests
retain source-file target paths, so navigation and diagnostics refer to the same
files opened from the source tree. Cargo package identities, dependency patches
and build-script output remain in the prepared workspace. Automatic targets and
explicit target options are retained; ignored files do not enter the build.

Native checks use the installed `hyper-cargo` driver and the module's declared
features. Bootstrap uses static linkage. Kernel and host tools retain their
ordinary Cargo configuration. Diagnostics and build-script checks share the
same repository-aware entry point. Their cache is separate from production
artifacts under `OUT/build/rust-analyzer/ARCH`.

Run preparation again after changing module registration, then reload the VS
Code window if it retains old settings. Changing a Rust source file does not
require regenerating the editor workspace.
