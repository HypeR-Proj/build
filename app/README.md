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

- a `no_std` Rust static PIE Native `init` supervisor;
- a bounded, declarative service manifest loaded by `init` from BootFs;
- direction-attenuated physical Console workers and an isolated foreground
  session manager;
- reproducible AArch64 compilation through the installed `hyper-cargo` driver;
- compilation exclusively against the assembled Native SDK; and
- end-to-end CI validation with the kernel from the same commit.

The Kernel starts only `/init` and supplies the minimum bootstrap authorities:
BootFs, process-construction authority, resource and task ownership, and a
Console capability. `init` validates `/etc/hyper/services.json` before it
starts any child, then constructs services in dependency order through the
transactional `ProcessBuilder` ABI. Normal bytes currently follow this route:

```text
physical Console <-> Console input/output workers <-> raw ByteChannels
                 <-> foreground session manager
```

Init retains management authority. Each Console worker receives only one
physical direction plus one matching byte-channel direction; the session
manager receives no physical Console authority. Capability attenuation is
monotonic and none of the children can duplicate or transfer these endpoints.
The two blocking workers provide a genuinely duplex data plane without
polling or a generic per-byte protocol header. Application code depends on the
safe `hyper-os` binding and does not call the raw syscall crate or C runtime
directly.

## Build

From the repository root, run:

```sh
make app
```

The generated SDK is placed under `target/sdk/aarch64`, and static PIE
application images are written to `target/app/aarch64`.

## Repository layout

```text
app/
  config/             Boot service manifest
  console/            Direction-attenuated physical data-plane workers
  init/               Native system bootstrap and supervision
  session/            Initial foreground-session policy
```

Reusable OS interaction belongs to `sdk/rust/hyper-os`; application-local
service and command policy remains under `app`.

## Roadmap

- add capability-rendezvous foreground-session handoff and WaitSet-backed
  multi-service supervision;
- add `ps` after typed process and thread inspection interfaces are public;
- add capability-aware diagnostics and administration utilities; and
- produce signed static PIE application images through HypeR Toolchain.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).
