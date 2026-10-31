<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# HypeR build tools

The manifest checks this repository out at the source workspace root. It owns
build adapters, composition tools and common development commands. Repository
paths and product descriptions belong to the [manifest](https://github.com/HypeR-Proj/manifest).

```sh
make prepare OUT=$PWD/out
make rebuild OUT=$PWD/out ARCH=aarch64
make -C app/top OUT=$PWD/out
make menuconfig OUT=$PWD/out
make test MODULE=build OUT=$PWD/out
```

Every normal Make invocation runs preparation automatically. Sources remain in
their repositories; generated Cargo workspaces, editor settings, configurations,
caches and images live under `OUT`. Open `out/HypeR-aarch64.code-workspace` in
VS Code after preparing the SDK with `make sdk`.

Each repository declares its build, tests, optional Kconfig and delivered files
in `component.json`. Adding a module updates that repository and the manifest;
it does not require a new hard-coded module list here or in HypeR's documentation.

See [getting started](docs/getting-started.md), [module metadata and outputs](docs/module-builds.md),
[incremental builds](docs/incremental-builds.md), and [Native composition](mk/README.md).
System acceptance is started manually in the [CI repository](https://github.com/HypeR-Proj/ci).
