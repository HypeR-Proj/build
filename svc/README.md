<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Formal background services

Build and deployment options are declared in this directory's [Kconfig](Kconfig).
Use the repository's `make menuconfig` and `.config` to select programs; see
[product configuration](../docs/usage/deployment/configuration.md) for dependencies and defconfig workflows.

These independent executables install in `/svc`. They share the formal Cargo
workspace in `app/Cargo.toml`; bootstrap implementations remain separately owned
under `bootstrap/stage1`. `component.mk` uses the same Native build template as
foreground programs.

`component-manager` is the formal root and owns component lifecycle and capability
routing. `system-startup` performs finite provider adoption and provisions the
initial environment, then exits. `journal` owns logs; `bootstrap-retire` owns final
predecessor teardown. Component-manager reports early startup status alongside
service logs until the first shell is released. Filesystem, I/O, VM and session
managers continue owning their domain workers. Those implementations
are not linked into component-manager.

See [components and supervision](../docs/design/services/components.md) for configuration and
administration, and [bootstrap handoff](../docs/design/boot/bootstrap-handoff.md) for ownership.
`/svc` is a deployment convention; component declarations can name other paths.
