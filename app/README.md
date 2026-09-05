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

- a static PIE Native `init` process with capability-scoped console echo;
- reproducible AArch64 compilation through `hyper-clang`;
- compilation exclusively against the assembled Native SDK; and
- end-to-end CI validation with the kernel from the same commit.

`init` consumes the startup metadata prepared by the Kernel, locates its
explicit Console capability, and waits for and echoes raw console input. It
does not acquire ambient access to a debug device or platform register bank.

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
  init/       Native system bootstrap and service supervision
```

Code shared by multiple utilities will be introduced under a dedicated common
module only after a concrete second consumer exists.

## Roadmap

- complete `init` process bootstrap and capability handoff;
- add service lifecycle and health supervision;
- add `ps` after typed process and thread inspection interfaces are public;
- add capability-aware diagnostics and administration utilities; and
- produce signed static PIE application images through HypeR Toolchain.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).
