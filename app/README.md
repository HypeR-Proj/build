<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# HypeR Native applications

This directory contains the capability-oriented system applications that
run directly on the HypeR Native ABI. Target builds consume the assembled
Native SDK; source-level host tests use workspace SDK patches. Applications
do not reach into kernel or runtime implementation internals.

VM management remains a distinct security boundary within this repository. A
long-lived fleet manager owns definitions and policy, isolated per-VM runtimes
own guest construction and execution handles, and a separate `vmm` client
receives only the control or runtime byte-channel authority needed for one command.

## Application development

The in-tree executables use the SDK's partial Rust std port. Command-line interfaces
use clap, shell words use shlex, and formatting, collections, paths, and ordinary
command output use std. `echo` keeps option-looking text literal, including
`--help` and `-n`; other public commands provide clap help and reject extra or
conflicting arguments. Shell builtins use `try_parse_from` so help and argument
errors return to the prompt rather than terminating the shell. Shell quoting
follows POSIX shlex rules, including comments and double-quote escaping.

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

## Current scope

- Rust std applications with ordinary `main`, built as Native dynamic PIE;
- a bounded, declarative service manifest loaded by `init` through a root
  `Directory` capability;
- direction-attenuated physical Console workers and an isolated foreground
  session manager;
- a bounded interactive shell which launches commands with explicit process
  authorities and handle-backed standard I/O;
- a capability-scoped VM manager, isolated runtime, and multi-client `vmm`
  control and virtual-console tool;
- reproducible AArch64 and RISC-V compilation through the installed `hyper-cargo` driver;
- compilation exclusively against the assembled Native SDK; and
- end-to-end CI validation with the kernel from the same commit.

The Kernel starts only `/init` and supplies the minimum bootstrap authorities:
the root `Directory`, process-construction authority, resource and task ownership, and a
Console capability. `init` validates `/etc/hyper/services.json` before it
starts any child, then constructs services in dependency order through the
transactional `ProcessBuilder` ABI. Normal bytes currently follow this route:

```text
physical Console <-> Console input/output workers <-> raw ByteChannels
                 <-> virtual console manager <-> shell <-> command
```

Init retains management authority. Each Console worker receives only one
physical direction plus one matching byte-channel direction; the session
manager receives no physical Console authority. Capability attenuation is
monotonic; the Console workers and virtual console manager cannot delegate their
physical transport endpoints. The manager creates fresh client channels before
starting a noncritical shell and supervises its lifetime. Shell exit or failure
restarts only that client, with a delay for rapid failures.
The two blocking workers provide a genuinely duplex data plane without
polling or a generic per-byte protocol header. Manifest purposes are symbolic,
image-scoped service-contract names; init resolves them to typed startup
purposes before creating any process. Application code depends on the safe
`hyper-os` binding and does not call the raw syscall crate or C runtime
directly.

Session and VM services also receive `stdio.output` and `stdio.error` write
endpoints, so ordinary `println!` and `eprintln!` work. These feed the Console
output worker directly, bypassing the session relay to avoid a dependency on
the logging service consuming its own queue. VM-manager duplicates these
streams into each VM-runtime; the Rust runtime owns them through process exit.
The output queue is bounded and applies backpressure when full. It carries no
read authority, and process termination closes that process's copies without
closing other writers. The shell retains its foreground streams. Console
workers keep only their existing transport endpoints and receive no standard
streams; other background services receive no stdin.

The manifest's optional `virtual-machines.config` path selects a file of named
VM definitions. Board packaging generates `/data/vms.json` and gates its use on
I/O runtime readiness; standalone tests use files under `init/tests/config/`.
Init transfers the configuration File to the VM manager. The manager validates
and opens guest images, then creates a fresh child resource domain, task group,
creation lease and isolated runtime for each start. There are up to eight
business definitions; the fleet also budgets a separate resident I/O VM.
Admission remains subject to available resources.

Each `vmm` invocation receives private control and capability channels. The
manager supports multiple clients and reports exhausted connection capacity.
A runtime owns its read-only shared serial ring, WaitSet subscriptions and
bounded output retention. Only one client may attach to a VM's console, while
other clients can still issue lifecycle requests. Guest output does not bypass
the runtime to reach the physical Console.

```text
vmm list
vmm status alpine
vmm start alpine
vmm console alpine
vmm affinity alpine 0 1,3
vmm stop alpine
vmm restart alpine
vmm create test --image /data/vm/alpine.itb
vmm delete test
```

No subcommand means `list`; VM operations require a name. Ctrl-] opens the
console detach menu. Nonblocking guest input and a bounded pending buffer keep
detach responsive even when the guest stops reading. Deployment configuration
is persistent; `create`/`delete` affect only the running manager. There is no
`save`/`load` command. See [Native applications](../docs/applications.md) for
console, affinity, read-only I/O VM observation and accounting semantics.

The shell supports bounded editing, quoting, `cd`, `pwd`, `help`, `clear` and
`exit`, external commands, concurrent pipelines and file redirection. `echo`
is an external application. It receives explicit process-construction authority
from the virtual console manager. Child commands inherit attenuated root and
cwd Directory capabilities, standard streams and authorized process resources;
path traversal remains bounded by the delegated root. This is not confinement
to the child's initial cwd. See [shell syntax and limits](../docs/shell.md).

## Build

From the repository root, run:

```sh
make app                       # AArch64
make app ARCH=riscv64           # RISC-V Native applications
make test-native ARCH=riscv64   # std, processes, threads, and shell acceptance
```

The generated SDK is placed under `target/sdk/aarch64`, and dynamic PIE
application images are written to `target/app/aarch64` by default. Applications
may set `HYPER_LINK_MODE=static` to select the SDK's equivalent `libhyper.a`
link path; the integration image includes and executes one static Rust command
as a contract test.

`ARCH=riscv64` selects separate SDK and app output directories and runs the same
service graph, including the userspace VM fleet. The optional
`init/config/native/services.json` profile contains console workers and the
virtual console manager without a VM fleet. The manager starts its shell.
Acceptance-only manifests live under `init/tests/config/`.
The same init and shell binaries support service graphs with or without a VM
manager; `vmm --help` does not require a running manager.

## Repository layout

```text
app/
  Cargo.toml          Workspace, dependency versions, and shared lints
  cat/ chmod/ cp/ echo/ free/ grep/ handle/ ln/ ls/ mkdir/ mv/ ps/ rm/ rmdir/ top/ touch/
  console-input/ console-output/
  init/
    config/           Production service template and standalone Native profile
    src/              Bootstrap and supervision
    tests/            Unit tests and acceptance-only config/ manifests
  session/
  shell/
  io-runtime/ vm-manager/ vm-runtime/ vm-smoke/ vmm/
  vm-support/         Shared guest-image, device and protocol mechanisms
  vm-policy/          Resource policy shared by init and the VM manager
```

Each executable is its own Cargo package with `src/main.rs`. Its clap types
and reusable implementation modules live in its own `src/`; unit-test bodies
live in its own `tests/` and are included by that package's library. Packages
set `autotests = false` so Cargo does not also treat these unit-test files as
standalone integration targets. No command tests are loaded by init.

Run all host tests with `make app-test`, or select a package with
`make app-test APP_TEST_PACKAGE=hyper-ls`. This source-level host-test entry
uses the workspace's SDK source patches and does not require SDK assembly. `make app-check` and `make app` cover
all workspace members. Target executable names and installed paths are unchanged.

Reusable OS interaction belongs to `sdk/rust/hyper-os`; application-local
service and command policy remains under `app`.

## File tools

The Native initramfs includes these independent std/clap applications in `/bin`:

| Command | Supported operations |
| --- | --- |
| `cp [-R] SOURCE... DEST` | Copy files or recursively copy trees; multiple sources require a directory. |
| `mv SOURCE... DEST` | Rename files, links, or directories; multiple sources require a directory. |
| `ln [-s] [-T] TARGET LINK` | Create hard or symbolic links; `-T` treats LINK as an exact name. |
| `rm [-rf] PATH...` | Remove files or trees; `-f` ignores missing paths. |
| `chmod [-R] MODE PATH...` | Octal modes or comma-separated symbolic clauses, such as `u+rw,go-rwx` and `a+X`. |
| `mkdir [-p] [-m OCTAL] PATH...` | Create directories and optional parents. Explicit modes apply to newly created final directories. |
| `rmdir PATH...` | Remove empty directories. |
| `touch [-acm] [-r REFERENCE] PATH...` | Create empty files or update file/directory timestamps without truncation. |

All tools accept `--help` and `--` before operands beginning with `-`, report
path-specific errors, and return failure if any requested operation fails.
`chmod` treats an omitted user/group/other selector as `a`; Native mode bits do
not imply a Unix credential or umask implementation. Recursive chmod skips
nested symbolic links; an explicitly named symbolic link changes its target.

Recursive `cp` preserves symbolic links, including dangling links. It refuses
existing destination symlinks, same-file copies (including hard links), and
copying a directory into itself. Regular destination files are overwritten.
`ln` refuses existing link names. `mv` uses filesystem rename and reports
cross-filesystem moves as unsupported by that operation; it does not silently
fall back to a non-atomic copy/delete sequence. Recursive `rm` does not follow
symbolic links and refuses the root and final `.`/`..` operands. These are basic
file tools, not full GNU coreutils option compatibility.

## Diagnostic commands

`ls` uses std filesystem APIs for supplied absolute or relative paths within
its delegated root and cwd capabilities. `ps` lists Processes by default,
including their immutable service label,
lifecycle state, and active/pending Thread counts. `ps --threads` (or `ps -T`)
also places every visible Thread directly below its owning Process and lists
kernel Threads with `kernel` as the owner; thread rows include their process
name and per-CPU idle threads use `idle/CPU`. `handle <process-koid>` decodes the
selected Process's handle kinds, rights, and object purposes; `handle
--objects` reports the visible kernel-object graph. Both commands require
explicit inspector capabilities, and every displayed KOID remains diagnostic
metadata rather than authority.

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
  inspection (`ps`, `handle`, `free`, `top`);
- console/session channel relays and multi-object waits, including the shell's
  foreground input handoff and `vmm console` multiplexing;
- absolute Native deadlines supplied to those waits and service protocols.

The shell emits its own prompts and diagnostics through std stdout and flushes
before blocking. Input ownership and child-channel routing remain Native;
buffering that input in std while handing its channel to another consumer
would require a separate handoff protocol. Init also uses std, retaining Native APIs for bootstrap and delegation.

## Planned work

The shared [roadmap](../docs/roadmap.md) owns project commitments. Existing
Native application functionality remains supported while I/O backend and
hardware qualification work continues. Multi-UART transport discovery and
wiring are not yet implemented; the current deployment has one physical console.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).
