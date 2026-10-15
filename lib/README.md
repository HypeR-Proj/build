<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Formal-system implementation libraries

This directory owns formal-system userspace implementations and reusable application
code. Bootstrap cannot depend on it. Common kernel interfaces and language-runtime
support live in [`stdlib/`](../stdlib/README.md). ABI definitions and SDK construction remain in `sdk/abi` and
`sdk/toolchain`; the assembled SDK remains the build boundary for applications.

| Source | Responsibility | Build owner |
| --- | --- | --- |
| [`dynamic-loader/`](dynamic-loader/README.md) | Formal static/dynamic executable loading, relocation and capability-relative runtime loading | SDK assembly through CMake |
| `service/`, `block/`, `vm-image/` | Service protocols, GPT parsing and guest-image handling | Formal Cargo workspace |

The application libraries below are explicit members of `app/Cargo.toml`, with
one lockfile, dependency graph, profile and installed-SDK boundary. They do not
depend on kernel source or source-tree runtime paths. Application-specific code
remains with its executable.

The minimal static bootstrap loader belongs to
[`bootstrap/stage1/userspace-loader`](../bootstrap/stage1/userspace-loader/README.md).
The kernel starts `/init` through that loader; later process builders choose
the bootstrap or formal loader explicitly through an executable capability.

| Library | Consumers | Responsibility |
| --- | --- | --- |
| `hyper-clap-shared` | All clap-based Native tools | Own the common clap parser, validation and help code; applications retain their argument types and derive expansions. |
| `hyper-tool-args` | `handle`, `ps`, `top`, `free` | Parse complete generation-bearing IDs and sampling intervals; combine process-ID and name selectors. Shared argument types live here; command-specific clap declarations remain in each tool. |
| `hyper-vm-policy` | System-startup, `vmm`, VM manager and runtime | VM resource limits, fleet configuration/control records, affinity policy and image validation. |
| `hyper-vm-support` | Formal VM runtime | Guest-image copying, virtual-device state, backend-control protocols and I/O guest construction. Process supervision remains in the services. |
| `hyper-component`, `hyper-service-ipc`, `hyper-journal` | Formal managers and administration tools | Bounded lifecycle/query wire types and journal registration; no service graph or domain implementation. |
| `hyper-component-config` | Component manager and startup coordinator | Bounded instance declarations, realm routing and rights attenuation validation, with no kernel bindings. |
| `hyper-bootstrap-handoff` | Formal component manager, startup coordinator and retirement job | Formal-side bootstrap ownership inventory and retirement wire contract; stage1 keeps its independent implementation. |
| `hyper-terminal` | Terminal service, session manager, shell and interactive tools | Terminal client API and wire contract: settings/control, foreground leases and events. Line discipline and queues are private to `svc/terminal-service`. Ordinary stdin/stdout/stderr users, bootstrap and stdlib do not depend on this library. |
| `hyper-rust-std` (in `stdlib/`) | The Rust shared libraries | Own one copy of Rust std and its dependencies so independent DSOs can be linked into the same process. |

`make app` builds the libraries and installs their `.so` artifacts under
`target/app/<arch>/lib/`. The delivered files are `libhyper_clap_shared.so`, `libhyper_tool_args_shared.so`,
`libhyper_vm_policy_shared.so`, `libhyper_vm_support_shared.so` and
`libhyper_rust_std.so`. Per-library `component.mk` declarations register them as
providers. Image composition follows ELF dependencies and installs needed libraries in
`/lib64/<arch>-hyper-hyper/` in both system and development images, with
`/lib -> lib64`. AArch64 therefore uses `/lib/aarch64-hyper-hyper/`;
RISC-V uses `/lib/riscv64-hyper-hyper/`. `libhyper.so` shares that directory.
ELF files retain the interpreter identity `/lib64/ld-hyper-<arch>.so`; process
launchers explicitly supply the formal loader from `/lib64/`. The independent system filesystem is the formal root.
Applications have actual ELF `DT_NEEDED` entries;
the existing Native interpreter resolves these names through its Directory
capability. Libraries acquire no capabilities or service authority of their own.

The terminal library remains an rlib. Formal service supervision belongs to
`svc/component-manager`; bootstrap owns separate supervisor and filesystem-worker implementations. The handoff contract
is exercised by the boot-system acceptance tests.

The clap delivery crate re-exports the unmodified upstream API and shares the
same std owner. Each policy/support implementation crate remains an rlib.
Its small `shared/` package re-exports
the same API as a Rust dylib. Dynamic Native consumers enable the shared-delivery
feature and select that package through target dependencies. Host tests use the implementation rlibs directly. Bootstrap uses its own
private implementations. This keeps Native
SDK calls out of host shared-library links without introducing FFI wrappers for the formal environment. The shared-delivery packages require dynamic linking.
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

See [Native component builds](../mk/README.md) for the shared Make templates,
feature variants, library collection and SDK component cache.
