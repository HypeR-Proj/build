<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# HypeR build tools

This repository provides Make rules, build scripts and development tools.
The [manifest](https://github.com/HypeR-Proj/manifest) checks it out at the
workspace root and places each source repository at its declared path.
Product configuration and workspace composition belong to the manifest.

From a synchronized workspace:

```sh
make native-initramfs ARCH=aarch64
python3 project.py test kernel
python3 project.py prepare
```

`prepare` records source commits and file hashes in `source-inventory.json`.
It does not copy sources or change Cargo manifests. Edit files in their checked-out
repositories and build them directly. `project.py archive OUTPUT.tar.xz` collects
all tracked project sources and manifest links for redistribution.

Each component owns its test commands in `component.json`; `{component}` is its
checkout and `{system}` is the workspace root. System acceptance is manually
started in the [CI repository](https://github.com/HypeR-Proj/ci).

See [HypeR](https://github.com/HypeR-Proj/HypeR) for setup and documentation.
