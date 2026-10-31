<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Native component composition

The root Makefile and module Makefiles enter the common build frontend.
`make prepare` creates an execution view under OUT and installs `mk/system.mk`
as its backend. Do not edit the generated view.

Each repository owns `component.json`, with Cargo membership, explicit build
steps and image artifact declarations. `mk/components.mk` translates these
metadata records into the delivery validator's input. Product Kconfig program
choices are generated from artifact defaults and runtime prerequisites; module
Kconfig namespaces stay independent.

```sh
make app-manifest
make -C app/top OUT=/work/hyper-output
make native-initramfs OUT=/work/hyper-output
make menuconfig OUT=/work/hyper-output
```

Cargo owns package dependencies and features. `artifacts.requires` expresses
installed-program prerequisites, independently of crate dependencies. Normal
products select enabled artifacts; development and I/O fixtures may explicitly
request additional profiles. Admission rejects missing required programs and
invalid storage/profile combinations before publication.

Stage 1 and formal userspace use distinct Cargo workspaces and output caches.
Their artifact `environment` is `boot` or `system`. Native SDK assembly and
immutable publication remain owned by toolchain adapters. The formal root
filesystem, boot archive and persistent board partitions retain their separate
packaging rules.

See [module metadata and outputs](../docs/module-builds.md),
[product configuration](https://hyper-proj.github.io/manifest/docs/configuration.html),
[board storage](../docs/deployment/board-storage.md), and
[system test cases](https://github.com/HypeR-Proj/system-tests).
