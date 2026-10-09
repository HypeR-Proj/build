<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Shared application libraries

These libraries hold implementations used by multiple Native applications. They
are explicit members of `app/Cargo.toml`, with one lockfile, dependency graph,
profile and SDK boundary. They do not depend on kernel source or source-tree SDK
paths. Application-specific code remains with its executable.

| Library | Consumers | Responsibility |
| --- | --- | --- |
| `hyper-tool-args` | `handle`, `ps`, `top`, `free` | Parse complete generation-bearing IDs and sampling intervals; combine process-ID and name selectors. Shared argument types live here; command-specific clap declarations remain in each tool. |
| `hyper-vm-policy` | `init`, `vmm`, VM manager and runtimes | VM resource limits, fleet configuration/control records, affinity policy and image validation. |
| `hyper-vm-support` | VM and I/O runtimes | Guest-image copying, virtual-device state, backend-control protocols and I/O guest construction. Process supervision remains in the services. |
| `hyper-rust-std` | The other Rust shared libraries | Own one copy of Rust std and its dependencies so independent DSOs can be linked into the same process. It uses the installed SDK's std port. |

`make app` builds the libraries and installs their `.so` artifacts under
`target/app/<arch>/lib/`. The delivered files are `libhyper_tool_args_shared.so`,
`libhyper_vm_policy_shared.so`, `libhyper_vm_support_shared.so` and
`libhyper_rust_std.so`. `app/deployment.json` installs them in
`/lib64/<arch>-hyper-hyper/` in both system and development images, with
`/lib -> lib64`. AArch64 therefore uses `/lib/aarch64-hyper-hyper/`; RISC-V uses
`/lib/riscv64-hyper-hyper/`. The interpreter and `libhyper.so` share that directory.
Applications have actual ELF `DT_NEEDED` entries;
the existing Native interpreter resolves these names through its Directory
capability. Libraries acquire no capabilities or service authority of their own.

Each implementation crate remains an rlib. Its small `shared/` package re-exports
the same API as a Rust dylib. Native consumers select that package through target
dependencies; host tests select the implementation directly. This keeps Native
SDK calls out of host shared-library links without introducing FFI wrappers or
duplicating implementations. These Native consumers require dynamic linking.
`make app-check`, `make app-test`, `make app-sdk-test`, and the Native API documentation include
the implementation crates automatically; the Native-only delivery packages are
compiled and exercised by the image and QEMU checks. For example:

```sh
make app-test APP_TEST_PACKAGE=hyper-tool-args
make app-test APP_TEST_PACKAGE=hyper-vm-support
```

The Rust ABI is internal to one product build. Build and deploy applications and
libraries together using the same pinned compiler, SDK, Cargo profile and feature
set. A library filename is not an independently versioned Rust ABI contract.
When adding another Rust shared library, link `hyper-rust-std` explicitly, as the
existing delivery packages do, to retain the single std owner. A delivery package
that depends on another shared implementation also links its delivery package;
`vm-support/shared` uses `vm-policy/shared` this way. Generic functions can
still be monomorphized in callers; declaring a dylib does not relocate every
instantiation out of its consumer.

Before image publication, the packer checks each ELF dependency graph against the
selected architecture's library directory, including archive symlink resolution,
architecture, SONAME and required dynamic
symbols. Missing or mismatched libraries fail the build before replacing the
previous image. Feature-specific fixtures must package a coherent set of binaries
and DSOs; copying only a newly built binary over an older library set can fail
this check. The fixture targets therefore use `make app` with `APP_FEATURES` and
an isolated `APP_OUTPUT` to stage the whole application/library set; extra probe
binaries are selected with `APP_EXTRA_BINS`. Board images take the I/O runtime
from this staged set, just like the other services.
