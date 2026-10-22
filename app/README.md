<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# HypeR Native applications

Build and deployment options are declared in this directory's [Kconfig](Kconfig).
Use the repository's `make menuconfig` and `.config` to select programs; see
[product configuration](../docs/usage/deployment/configuration.md) for dependencies and defconfig workflows.

This directory contains foreground Native tools. Background programs live in
[`svc/`](../svc/README.md), with independent service policy and logging.
The RAM-resident boot and recovery programs live in
[`bootstrap/stage1`](../bootstrap/stage1/README.md), an independent static-link
workspace with its own lockfile and executable-owned implementations. The workspace here owns
formal applications and reusable libraries in the top-level `lib/`.
Native acceptance executables live under [`tests/app`](../tests/app/README.md);
they share this workspace's dependency lock but are built or packaged only by
their selected test targets or development profile.
Target builds consume the assembled
Native SDK; source-level host tests use workspace SDK patches. Applications
do not reach into kernel or runtime implementation internals.

VM management remains a distinct security boundary within this repository. A
long-lived fleet manager owns definitions and policy, isolated per-VM runtimes
own guest construction and execution handles, and a separate `vmm` client
receives only the control or runtime byte-channel authority needed for one command.

## Application development

The in-tree executables use the SDK's partial Rust std port. Application
command-line interfaces use clap; the formal shell retains quotes in its own
lexer/parser until expansion. Formatting, collections, paths and ordinary
command output use std. `echo` supports `-n`, `-e` and `-E`; unknown options such
as `--help` are text, and the first operand ends option parsing. Other public
commands provide clap help and reject extra or conflicting arguments. Shell
syntax and builtin errors return to the interactive prompt.

`hyper_rt::process::startup()` transfers Native capabilities once to a std
application. The runtime retains standard streams and the bootstrap Console
through std cleanup and TLS destruction. Use `std::io` for ordinary output;
borrowed runtime handles are available for Native readiness waits and routing.

Use `std::thread` and `std::sync` for application threads and synchronization;
these now use Native thread and atomic wait/wake syscalls. Ordinary sleeps use
`std::thread::sleep`. Absolute deadlines passed to Native object waits retain
the SDK clock types, since std does not expose that capability wait contract.

Ordinary path-based filesystem and subprocess operations can use std. Explicit
directory/process capability delegation, VM operations and service channel
multiplexing use hyper-os because std does not represent those authority APIs.
The bootstrap manifest keeps its restrictive schema parser (including duplicate
field and escape rejection); its bounded collections now use Vec storage.
`make app-check`, `make app-test`, and `make test-native` validate the migration.

## Runtime architecture

The kernel starts the archive's `/init` through the bootstrap loader. Stage-1
prepares an independent system root and transfers supervision to the formal
component manager. The detailed ownership rules live in
[boot architecture](../docs/architecture/boot-layout.md) and the
[handoff contract](../docs/design/boot/bootstrap-handoff.md).

Formal commands use [terminal streams and scoped authority](../docs/usage/applications/execution.md).
Background providers follow the [component contract](../docs/design/services/components.md);
VM lifecycle and console operations follow the [vmm manual](../docs/usage/applications/vmm.md).
These manuals own runtime behavior; this README describes how the application
workspace is built and maintained.

## Build

From the repository root, run:

```sh
make app                       # AArch64
make app ARCH=riscv64           # RISC-V Native applications
make test-native ARCH=riscv64   # std, processes, threads, and shell acceptance
```

The generated SDK is placed under `target/sdk/aarch64`, and dynamic PIE
application images are written to `target/app/aarch64` by default. Applications
that have no Rust DSO dependencies may set `HYPER_LINK_MODE=static` to select
the SDK's equivalent `libhyper.a` link path. The system tools and VM services
that consume `lib/` use dynamic linking; the integration image still executes
`echo-static`, a standalone C fixture, as the SDK static-link contract test.
Rust static linking remains covered by `std-test-static`. Both static and
dynamic system executables use the explicitly delegated
`/lib64/ld-hyper-<arch>.so` loader. The static userspace loader under
`/boot` serves bootstrap programs only.

Clap-based tools select `libhyper_clap_shared.so` for Native builds; the same
upstream parser is used directly for host tests. Command-specific argument
structures stay in each tool. Component declarations and shared build templates
are described in [Native component builds](../mk/README.md).

`ARCH=riscv64` selects separate SDK and app output directories and runs the same
service graph, including the userspace VM fleet. The optional
`bootstrap/stage1/init/config/services-console-only.json` profile contains the raw Console service without a VM fleet. Formal init
starts the terminal and shell through session-manager; failed bootstrap starts
the independent fallback shell.
Acceptance-only manifests live under `bootstrap/stage1/init/tests/config/`.
The same init and shell binaries support service graphs with or without a VM
manager; `vmm --help` does not require a running manager.

## Repository layout

```text
app/
  Cargo.toml          App discovery, shared dependency versions, lints and profiles
  Cargo.lock          Locked dependencies for the application and Rust DSO set
  cat/ chmod/ cp/ echo/ editor/ free/ grep/ handle/ ldd/ less/ ln/ ls/
  memstat/ mkdir/ mount/ mv/ ps/ rm/ rmdir/ shell/ top/ touch/ umount/ vmm/
  componentctl/ journalctl/
svc/
  component-manager/ system-startup/ bootstrap-retire/ journal/
  console-raw/ session-manager/ terminal-service/
  io-runtime/ fs-backend/ fs-fat/ fs-erofs/ vm-manager/ vm-runtime/
bootstrap/stage1/
  init/               Initial service graph and supervision handoff
    config/           Service manifests for VM-enabled and console-only startup
    tests/            Unit tests and acceptance-only configurations
  console-raw/ fallback-sh/
  io-runtime/ fs-backend/ fs-fat/ fs-erofs/ mount/ umount/
  userspace-loader/   Static bootstrap executable loader
lib/                  Shared application libraries, in the app Cargo workspace
  clap/               Common command-line parser DSO
  tool-args/          Argument parsing and process selection for system tools
  vm-policy/          VM fleet configuration, resource policy and image validation
  vm-support/         Guest loading, virtual devices and I/O backend protocols
  terminal/           Formal terminal client and wire contract
  text-ui/            Shared fullscreen input/display helpers for interactive text tools
  dynamic-loader/     Formal executable loader and runtime linker
stdlib/rust-std/      Common Rust standard-library DSO
tests/app/            Standalone Native acceptance programs
  vm-smoke/ terminal-probe/ fs-failure/
```

Each executable is its own Cargo package with `Cargo.toml` and `src/main.rs`.
The workspace discovers app directories automatically, excluding `.cargo/` and
`target/`. A new app declares its build inputs in its own manifest and its
installed name/profile in `component.mk`. App-specific dependencies stay in that
app's manifest; shared dependencies and build policy stay in the workspace.
Source-only edits do not change `Cargo.lock`.

Command-specific clap types and reusable implementation modules live in the
app's own `src/`; unit-test bodies
live in its own `tests/` and are included by that package's library. Packages
set `autotests = false` so Cargo does not also treat these unit-test files as
standalone integration targets. No command tests are loaded by init.

Run all host tests with `make app-test`, or select a package with
`make app-test APP_TEST_PACKAGE=hyper-ls`. This source-level host-test entry
uses the workspace's SDK source patches and does not require SDK assembly. `make app-check` and `make app` cover
all workspace members. Component staged names are independent of their
bootstrap or system installation directory.

Reusable OS interaction belongs to `stdlib/rust/hyper-os`; application-local
service and command policy remains under `app`. Shared application implementations
live in [`lib/`](../lib/README.md), and their Native DSOs are installed in
`/lib64/<arch>-hyper-hyper/`, also accessible through `/lib`.
Applications and Rust DSOs must be built and deployed together with the same
SDK, compiler, profile and features. Host tests use the rlib variants.

## System services

These executables consume startup capabilities and service channels supplied by
their supervisor. Their installed paths identify service roles; they are not
standalone shell commands. The selected deployment profile determines which
services are present. See [init configuration](../bootstrap/stage1/init/config/README.md) for the
manifest and board configuration inputs.

Bootstrap paths below identify initial launch locations. Their independent
formal replacements live on the independent root; they do not retain bootstrap loaders,
worker binaries or configuration directories. There are no root-level service or
tool aliases. Each launcher receives its own `process.environment-directory` for
configuration and workers, separately from the live root namespace capability.

| Installed executable | Source | Responsibility |
| --- | --- | --- |
| `/init` | [init](../bootstrap/stage1/init) | Validate the service manifest, construct the service graph, delegate capabilities and supervise services. Start console services before waiting for storage; keep Native services available if storage or fleet configuration is unavailable. |
| `/svc/console-raw` in each environment | [bootstrap](../bootstrap/stage1/console-raw), [formal](../svc/console-raw/) | Own physical Console byte transport and independent bounded log ingress. Preserve raw bytes; mirror service logs during and after bootstrap. |
| `/svc/session-manager` | [session-manager](../svc/session-manager/) | Supervise the formal terminal and its shell/session group; revoke old transport bindings and restart a fresh session after failure. Does not relay ordinary I/O. |
| `/svc/terminal-service` | [terminal-service](../svc/terminal-service/) | Serve one terminal endpoint: canonical/raw input, editing and echo, conversions, software flow control, scoped foreground leases, size/events and ordered output. |
| `/svc/io-runtime` in each environment | [bootstrap](../bootstrap/stage1/io-runtime), [formal](../svc/io-runtime/) | Load the board-configured Linux I/O VM, manage its assigned physical devices and backend connections, discover GPT partitions, bind authorized block ranges and transfer Native block capabilities to the filesystem manager. Forward aggregate mount readiness. Broker guest block/network connections and retire their backend resources. Included in I/O deployments. |
| `/svc/vm-manager` | [vm-manager](../svc/vm-manager/) | Own named VM definitions and lifecycle policy, handle `vmm` requests, launch and supervise per-VM runtimes, and expose the I/O VM's read-only observation entry. |
| `/svc/fs-backend` in each environment | [bootstrap](../bootstrap/stage1/fs-backend), [formal](../svc/fs-backend/) | Apply fstab, identify the filesystem on each authorized GPT partition, load its independent worker, publish mounts and serve mount/umount requests. Retain lifecycle ownership outside the file I/O path. |
| `/svc/fs-fat` in each environment | [bootstrap](../bootstrap/stage1/fs-fat), [formal](../svc/fs-fat/) | Validate and serve FAT32 media. Uses the common filesystem service library for shared-buffer RPC and parent failure; the kernel retains VFS, file locks and caching. Started on demand by the manager in its own environment; the formal worker takes over existing FAT mounts before bootstrap retirement. |
| `/svc/fs-erofs` in each environment | [bootstrap](../bootstrap/stage1/fs-erofs), [formal](../svc/fs-erofs/) | Serve immutable core EROFS media, including directory and symlink metadata. Validate checksums and bounds, and reject unsupported format extensions. Started on demand by fs-backend; the formal worker takes over the mounted system volume. |
| `/svc/component-manager` | [component-manager](../svc/component-manager/) | Act as formal root, validate instance trees and capability routes, activate providers on demand and own component lifetimes. |
| `/svc/system-startup` | [system-startup](../svc/system-startup/) | Perform finite bootstrap adoption, formal mounts and initial VM provisioning. Submit prepared processes to the manager and arrange retirement, then exit. |
| `/svc/journal` | [journal](../svc/journal/) | Capture attributed service streams, retain bounded volatile records, mirror serial output and answer queries. |
| `/svc/bootstrap-retire` | [bootstrap-retire](../svc/bootstrap-retire/) | Stop and join transferred predecessors, drain console output and normally unmount the boot archive. |
| `/svc/vm-runtime` | [vm-runtime](../svc/vm-runtime/) | Construct and run one managed guest: load its image, configure vCPUs and virtual devices, connect I/O backends, serve its console and control requests, and retire resources on termination. Started by the VM manager for each VM start. |

The resident Linux I/O VM runs physical device drivers. `io-runtime` is the
Native service that manages that VM; `vm-runtime` manages an individual business
guest. [vm-smoke](../tests/app/vm-smoke/README.md) is a separate privileged acceptance fixture,
not an interactive command or production service.

## Command manuals

User-facing syntax and operational guidance live in the
[application manuals](../docs/usage/applications/README.md). This README covers the source
workspace, build process and development interfaces.


## Standard library and Native services

Use std for ordinary files, standard output, argument handling, allocation,
threads, synchronization and elapsed-time policy. File tools already follow
this boundary. The VM manager reads its supplied configuration through std,
and the VM runtime reads its supplied image through `std::fs::File` and
`std::os::hyper::fs::FileExt`. Both consume the original capability through
`hyper_os::fs::File::into_std()`; they do not reopen it by an ambient path.
The runtime retains the READ-authorized Native image-length query because std
metadata requires INSPECT, which its image capability deliberately lacks.

Native SDK calls remain where their semantics are required:

- VM construction, guest VMO writes, virtual serial and VM lifecycle;
- capability transfer, resource domains, scoped process construction and
  inspection (`ps`, `handle`, `free`, `memstat`, `top`);
- terminal leases, request-sized streams and multi-object waits, including the
  shell's foreground input handoff and `vmm console` multiplexing;
- absolute Native deadlines supplied to those waits and service protocols.

The shell emits its own prompts and diagnostics through std stdout and flushes
before blocking. Input ownership and child-channel routing remain Native.
Terminal stdin uses request-sized reads so handing foreground ownership to a
child does not strand unread bytes in a shell-local buffer. Init also uses std,
retaining Native APIs for bootstrap and delegation.

## Planned work

The shared [roadmap](../docs/project/roadmap.md) owns project commitments. Existing
Native application functionality remains supported while I/O backend and
hardware qualification work continues. Multi-UART transport discovery and
wiring are not yet implemented; the current deployment has one physical console.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).
