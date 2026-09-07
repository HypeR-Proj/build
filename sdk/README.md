<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Native SDK contract

The HypeR Native SDK is the supported build boundary between this source tree
and Native EL0 applications. Kernel, ABI, runtime, toolchain, and application
changes are reviewed and tested in one commit; SDK releases are derived from
that coherent commit rather than assembled from independently moving
repositories.

## Source ownership

The SDK is produced from five independently owned source components:

| Path | Responsibility |
| --- | --- |
| `sdk/abi/` | Machine-visible values, layouts, syscall metadata, and generated interfaces |
| `sdk/lib/` | Freestanding C runtime, startup code, and architecture syscall veneers |
| `sdk/loader/` | Capability-relative AArch64 runtime linker and `dlopen` implementation |
| `sdk/rust/` | Raw Rust ABI bindings, safe Native OS interfaces, and Rust runtime entry |
| `sdk/toolchain/` | Clang driver, linker script, ELF branding, and transactional SDK assembly |

The kernel consumes `sdk/abi/` as a dependency-free `no_std` path dependency.
Native applications consume only the assembled SDK. They must not include
headers from `sdk/abi/` or `sdk/lib/` directly, link source-tree archives, or
depend on private kernel modules.

## Build and installed layout

Run:

```sh
make sdk
```

The default output is `target/sdk/aarch64`:

```text
bin/hyper-clang
bin/hyper-cargo
bin/hyper-brand-elf
include/hyper/native.h
include/hyper/dlfcn.h
include/hyper/startup.h
include/hyper/syscall.h
include/string.h
lib/crt1.o
lib/crt.o
lib/libhyper.a
lib/libhyper.so
lib/ld-hyper-aarch64.so
lib/hyper/aarch64/hyper-native.ld
share/hyper/abi/Cargo.toml
share/hyper/abi/src/
share/hyper/rust/hyper-sys/
share/hyper/rust/hyper-os/
share/hyper/rust/hyper-rt/
share/hyper/rust/hyper-service/
share/hyper/manifest
```

`hyper-clang` accepts `HYPER_SYSROOT` to select another installed SDK and
`HYPER_CLANG` or `HYPER_LD` to select explicit LLVM tools. The SDK currently
targets AArch64 Native applications. `hyper-brand-elf` is a host executable,
so a published archive is identified by both its host and target platform.
The manifest records the SDK version, source revision, host, target, and Native
ABI revision. Local builds use a dirty-aware Git description; release jobs set
an explicit SDK version and source identity.

`hyper-cargo` builds `no_std` Rust applications against only the crates
installed in the selected SDK. It configures the AArch64 bare-metal code
generation target, the HypeR linker, dynamic PIE relocation, panic abort, and
installed-crate overrides. Repository builds additionally pass `--offline` to
make the producer-consumer check independent of a package registry; external
applications may use other Rust dependencies under their own policy. The
resulting ELF is branded and validated by the same final link path as a C
application. The built-in
`aarch64-unknown-none` compiler target supplies `core`; HypeR OS identity is
carried by the validated ELF ABI rather than by pretending to implement
another operating system target.

`hyper-clang -shared` produces capability-loadable shared objects with the
same W^X, page-alignment, and ELF-branding checks as applications. Shared
objects must export their public entry points explicitly because the compiler
driver uses hidden visibility by default.

Dynamic linking is the default. The generated executable names
`/lib/ld-hyper-aarch64.so` in `PT_INTERP` and records `libhyper.so` as its
runtime dependency. The interpreter performs eager `RELA`/`RELR` relocation,
enforces W^X and RELRO, and opens exact dependency names through the process's
delegated library Directory rather than a global path namespace. C consumers
may use `<hyper/dlfcn.h>` for capability-relative `hyper_dlopen_at`, `dlsym`,
and logical close. `HYPER_LINK_MODE=static` selects the matching
`libhyper.a` runtime and produces a freestanding static PIE with no interpreter
or runtime dependency. The dynamic and static libraries are built from the
same runtime sources and are both supported SDK application link modes.

`make sdk-check` validates generated ABI output, lints the Rust SDK crates,
builds the SDK transactionally, and compiles and links public-interface-only C
and Rust applications in both dynamic and static modes. The link contract
checks require dynamic images to carry an interpreter and runtime dependency,
and static images to carry neither. `make sdk-test` runs ABI layout tests,
safe-binding host tests, and portable C runtime unit tests.

## Application integration

`make app` first assembles the SDK and then builds the Rust init, two Console
data-plane workers, session manager, shell, and commands with the installed
`bin/hyper-cargo`. `make native-initramfs` packages them as one deterministic
`newc` archive, and `make test-native` boots the kernel and verifies that the
shell creates external Processes, executes a constructor-bearing `dlopen`
fixture, and routes output through the complete handle-backed Console path
under QEMU. The same test launches a statically linked Rust command to verify
that the installed `libhyper.a` path remains executable, not merely linkable.

This enforced producer-consumer path is also the release boundary: a source
change which works only through an undeclared source-tree include cannot pass
the Native integration job.

## Release model

Until the first supported SDK release, the Native ABI revision remains zero
and interfaces may change with the repository. A future GitHub release will
publish host-specific SDK archives built from the release tag. Each archive
will record the repository commit, host platform, target architecture, and
LLVM compatibility range. SDK release versioning is separate from the ABI
revision; publishing a toolchain package does not by itself declare ABI
stability.

Linux, FreeBSD, POSIX, and other foreign interfaces are not part of this SDK.
They remain separately versioned compatibility personalities built above the
HypeR Native capability boundary.
