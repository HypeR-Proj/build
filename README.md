<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Build integration

`project.py` prepares a disposable system build directory from a repo-managed source workspace.
It uses each repository's `component.json` ownership and Cargo context declarations; there is no
fallback to the original monorepo. Build inputs and their commits are recorded in `source-inventory.json`.

From the workspace root:

```sh
python3 build/project.py prepare
python3 build/project.py test kernel
python3 build/project.py make native-initramfs ARCH=aarch64
```

The default output is `out/system`. `--output PATH` selects another owned output directory.
Existing directories without the build marker are refused. Re-preparation preserves unchanged
file timestamps and removes only previously generated files.

Components own their test command lists. `{component}` expands to the source checkout and
`{system}` to the generated build directory. Module tests run with dependencies present but do
not implicitly invoke system acceptance. Use `HypeR/hyper` for the common developer entry point.
