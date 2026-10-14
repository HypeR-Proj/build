<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Native standard libraries

`stdlib/` owns the common substrate for kernel facilities and language runtime
entry. Bootstrap and the formal system may both use this layer, through the
installed SDK. It must not depend on either environment's service policy.

| Source | Responsibility |
| --- | --- |
| [`hyper/`](hyper/README.md) | Native C ABI wrappers, startup, heap/thread support and the Rust std C adapter |
| [`rust/`](rust/README.md) | Raw and safe Native bindings, Rust entry, and the kernel filesystem-service wire protocol |
| `rust-std/` | Formal-system dylib delivery of the SDK's Rust std port; bootstrap still links std statically |

ABI definitions remain in `sdk/abi/`. Compiler drivers and assembly of an
installed SDK remain in `sdk/toolchain/`. The Rust std PAL is versioned with that
toolchain; it adapts kernel interfaces, not system-service implementations.
The source-directory split does not change installed library names or paths.

`lib/` owns formal-system implementation libraries. `bootstrap/stage1/` owns
its own implementations and Cargo lockfile. Bootstrap may depend on this
substrate and third-party Cargo libraries, but never on
`lib/`, even through an optional, build, target-specific or vendored dependency.
`tests/ci/check-workspace.py` checks these edges and the substrate's own
transitive path dependencies. `make app-fetch`, and therefore Native build and
check targets, run the check before dependency resolution.

Filesystem recognition, FAT/EROFS engines, service supervision, VM deployment
policy, guest-image parsing and ELF loading are userspace implementations.
They do not belong here merely because bootstrap needs them. Bootstrap owns
its implementations under the owning stage-1 executable and `userspace-loader/elf/`;
formal implementations live under `app/` and `lib/`. Communication between the
environments uses explicit capability and wire contracts, not Rust type sharing.
