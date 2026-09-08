<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# HypeR Native applications

This directory contains the capability-oriented system applications that
run directly on the HypeR Native ABI. Applications consume only the assembled
Native SDK; they do not reach into Kernel, ABI, or Lib
implementation sources.

The VMM remains a distinct security boundary and subsystem even though its
future source will share this repository.

## Current scope

- a `no_std` Rust dynamic PIE Native `init` supervisor;
- a bounded, declarative service manifest loaded by `init` through a root
  `Directory` capability;
- direction-attenuated physical Console workers and an isolated foreground
  session manager;
- a bounded interactive shell which launches commands with explicit process
  authorities and handle-backed standard I/O;
- reproducible AArch64 compilation through the installed `hyper-cargo` driver;
- compilation exclusively against the assembled Native SDK; and
- end-to-end CI validation with the kernel from the same commit.

The Kernel starts only `/init` and supplies the minimum bootstrap authorities:
the root `Directory`, process-construction authority, resource and task ownership, and a
Console capability. `init` validates `/etc/hyper/services.json` before it
starts any child, then constructs services in dependency order through the
transactional `ProcessBuilder` ABI. Normal bytes currently follow this route:

```text
physical Console <-> Console input/output workers <-> raw ByteChannels
                 <-> foreground session manager <-> shell <-> command
```

Init retains management authority. Each Console worker receives only one
physical direction plus one matching byte-channel direction; the session
manager receives no physical Console authority. Capability attenuation is
monotonic and none of the children can duplicate or transfer these endpoints.
The two blocking workers provide a genuinely duplex data plane without
polling or a generic per-byte protocol header. Manifest purposes are symbolic,
image-scoped service-contract names; init resolves them to typed startup
purposes before creating any process. Application code depends on the safe
`hyper-os` binding and does not call the raw syscall crate or C runtime
directly.

The same manifest selects the initial guest through an `initial-vm.image`
path. Init validates this as a canonical absolute path, opens it through its
root Directory authority, and transfers the resulting opaque File capability
to the unique service which declares the VM provisioning contract. Neither the
VM manager nor the runtime infers guest identity from a built-in path or
process name.

The initial shell provides bounded line editing, quoting and escaping, `cd`,
`pwd`, `help`, `echo`, `clear`, and `exit`, plus external command launch from `/bin`.
It does not receive ambient process creation: init delegates an immutable root
`Directory` plus attenuated TaskFactory, TaskGroup, and ResourceDomain handles.
The shell keeps the root private, resolves parent-directory changes itself, and
gives each command only a read-only handle rooted at its current directory.
Commands cannot use `..` to acquire parent authority. Each command also receives
fresh ByteChannel endpoints under the standard typed I/O contract. The shell
waits on command output, input, and process termination in one kernel-backed
multi-object wait and inspects the Process handle for its terminal result.

## Build

From the repository root, run:

```sh
make app
```

The generated SDK is placed under `target/sdk/aarch64`, and dynamic PIE
application images are written to `target/app/aarch64` by default. Applications
may set `HYPER_LINK_MODE=static` to select the SDK's equivalent `libhyper.a`
link path; the integration image includes and executes one static Rust command
as a contract test.

## Repository layout

```text
app/
  config/             Boot service manifest
  console/            Direction-attenuated physical data-plane workers
  command/            Small standalone Native commands
  init/               Native system bootstrap and supervision
  session/            Initial foreground-session policy
  shell/              Interactive command parsing and process launch
```

Reusable OS interaction belongs to `sdk/rust/hyper-os`; application-local
service and command policy remains under `app`.

## Diagnostic commands

`ls` enumerates the current Directory capability, or one relative descendant,
without receiving the shell's root authority. `ps` lists Processes by default,
including their immutable service label,
lifecycle state, and active/pending Thread counts. `ps --threads` (or `ps -T`)
also places every visible Thread directly below its owning Process and lists
kernel Threads with `-` as the owner. `handle <process-koid>` decodes the
selected Process's handle kinds, rights, and object purposes; `handle
--objects` reports the visible kernel-object graph. Both commands require
explicit inspector capabilities, and every displayed KOID remains diagnostic
metadata rather than authority.

## Roadmap

- add capability-rendezvous foreground-session handoff and WaitSet-backed
  multi-service supervision;
- grow the command set around typed service APIs without introducing ambient
  namespaces or a generic message envelope;
- extend capability-aware diagnostics beyond the existing `ps` and `handle`
  tools;
- add command help and stable machine-readable output modes; and
- produce signed Native application images through HypeR Toolchain.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).
