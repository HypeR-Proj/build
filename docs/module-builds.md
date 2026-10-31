<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Repository builds and generated workspaces

## Sources and preparation

`repo init` and `repo sync` create the source layout declared by the manifest.
The manifest has its single checkout in `.repo/manifests`; the documentation-only
HypeR repository is not downloaded. The build repository occupies the workspace
root. Run `make prepare` explicitly, or let any normal Make target prepare first.

Preparation discovers `component.json` in each manifest project, checks build
dependencies and generates an execution view under `OUT/workspace/ARCH`.
Source bodies are linked to their owning checkout; build control files are
copied, and workspace manifests are generated from declarations. Edit source
repositories, not generated control files. Unchanged generated files retain their
timestamps. Preparation repairs altered or missing generated controls.

A source archive made with `python3 project.py archive OUTPUT.tar.xz` includes
module sources, the manifest and provenance. It can prepare its own output view
without Git or a second manifest checkout. Archives exclude generated build data.

## Output ownership

`OUT` defaults to the workspace's `out` directory. Pass an absolute path when
using the same directory from several module checkouts:

```sh
make prepare OUT=/work/hyper-output
make -C app/top OUT=/work/hyper-output
make -C kernel OUT=/work/hyper-output ARCH=riscv64
make test MODULE=build OUT=/work/hyper-output
```

| Location inside OUT | Contents |
| --- | --- |
| `workspace/ARCH` | Generated execution view |
| `config/ARCH/.config` | Product selection |
| `config/ARCH/modules/NAME.config` | Independent module configuration |
| `config/ARCH/generated/NAME` | Resolved JSON values and C header |
| `sdk/ARCH` | Published Native SDK |
| `build/NAME` | Module build outputs |
| `build/native/ARCH` | Shared Native Cargo caches, separated by workspace and product variant |
| `staging/modules/NAME` | A standalone module's installed artifacts |
| `fixtures`, `results`, `tmp`, `state` | Test fixtures, reports, temporary files and preparation state |
| `HypeR-ARCH.code-workspace` | VS Code entry for that prepared architecture |

One OUT belongs to one source workspace. Configurations and views are separated
by architecture. Preparation and configuration changes take an exclusive lock;
unchanged build consumers share it. Independent producers must still not publish
the same final image concurrently. Root `make clean` removes build outputs and
images inside OUT while preserving saved configurations and source repositories.
Module-level `clean` is intentionally unsupported. Use a separate OUT when an
independent scratch build is needed.

## Module description

Existing `name`, `version`, `packages`, `tests` and license fields remain. Build
recipes contain argument arrays, not shell fragments. For example:

```json
{
  "version": 1,
  "name": "example",
  "config": {"schema": "Kconfig"},
  "build": {
    "requires": ["toolchain"],
    "actions": {
      "all": [["python3", "{source}/build.py", "--output", "{out}", "--config", "{config}"]]
    }
  },
  "tests": [["python3", "{component}/tests.py"]]
}
```

`build.requires` is an acyclic list of module names; prerequisites run before the
selected module. `build.actions` lists supported Make goals. `build.steps` is an
alternative generic adapter receiving `{action}`. `build.environment` supplies
module-local environment additions. `build.makefile` selects an existing backend
Makefile for the generated view; the source checkout keeps its common frontend.

Recipes can use `{workspace}`, `{source}`, `{out}`, `{output}`, `{arch}`, `{action}`,
`{module}`, `{sdk}`, `{config}` and `{config_header}`. Here `{out}` is the module's
output directory; `{output}` is the common OUT. Tests use `{component}` and
`{system}`. Build steps run in the prepared workspace, with `OUT`, `ARCH`,
`HYPER_WORKSPACE`, `HYPER_BUILD_OUTPUT` and temporary-directory settings supplied.

A module without a build recipe has no build work. A module without tests reports
that explicitly on `make test MODULE=NAME`. PR module tests use these declarations;
system acceptance is a separate, manually selected operation.

## Independent Kconfig namespaces

A repository may provide `Kconfig` without extra metadata. To use another file,
set `config.schema`; `config.defaults` optionally names an architecture-aware
fragment, such as `configs/{arch}_defconfig`. Set `config` to `null` to disable
configuration discovery. A repository without Kconfig is non-configurable.

Root `make menuconfig` offers product selection and module menus. A module can
also use `make -C PATH menuconfig`. Every module is resolved separately and gets
its own saved file, exported values and generated header. Two repositories may
both define `CONFIG_DEBUG`; they do not see each other's value. The build process
receives only its own module's `CONFIG_*` values. Dependencies do not inherit them.

Product installation choices are generated separately from artifact declarations.
They select programs and their deployment prerequisites; they do not merge
module Kconfig files into a shared symbol namespace.

## Cargo membership and delivered files

`cargo` entries declare a repository-local manifest and a shared domain (`system`
or `boot`). Preparation constructs the workspace membership and path dependencies
from these entries and build policy templates. Cargo owns package dependencies,
features and compiler freshness.

`artifacts` declare one of `binary`, `library`, `source` or `symlink`, together with
its installed `destination`, mode, environment and profiles. Rust binaries also
specify `manifest`, `staged`, `rust_runtime`, a default `enabled` boolean and any
runtime `requires` entries (`module` plus `artifact`). Installation paths are
relative to the image root; declarations cannot escape it. See the
[top module](https://github.com/HypeR-Proj/top/blob/main/component.json) for a small
Native example. External programs can declare an explicit build recipe and a
`source` artifact without joining a Cargo workspace.

`workspace.files` projects repository-owned policy files into the generated view;
`workspace.pythonpath` exposes framework Python packages there. Repository paths
remain the manifest's responsibility. These mappings do not install files into
the system image.
