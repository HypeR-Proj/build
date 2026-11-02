<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Repository builds and generated workspaces

## Sources and preparation

`repo init` and `repo sync` create the source layout declared by the manifest.
The manifest has its single checkout in `.repo/manifests`; the documentation-only
HypeR repository is not downloaded. The build repository occupies `build/system/`; the workspace root is not a Git
repository. The manifest creates `Makefile` and `project.py` entry links to the
build checkout. Run `make prepare` explicitly, or let any normal Make target prepare first.
`make run` and `make board-run` launch existing images without scanning compiler
inputs or querying every source repository. They still validate configuration,
module declarations, launch controls and output links; changed controls or file
inventories trigger preparation. Source-body edits are picked up by the next
build. Launching never builds images or refreshes a persistent disk implicitly.

Preparation discovers `component.json` in each manifest project, checks build
dependencies and generates an execution view under `OUT/workspace/ARCH`.
The independent `fit-pack` repository lives at `build/tools/fit-pack`, and the
optional Raspberry Pi publisher lives at `build/pi5-images`.
Source bodies are linked to their owning checkout; build control files are
copied, and workspace manifests are generated from declarations. Edit source
repositories, not generated control files. Unchanged generated files retain their
timestamps. Preparation repairs altered or missing generated controls.

A source archive made with `python3 project.py archive OUTPUT.tar.xz` includes
module sources, the manifest and provenance. It can prepare its own output view
without Git or a second manifest checkout. Archives exclude generated build data.

`python3 project.py check-docs PATH` validates a repository's local Markdown
links using the discovered `docs` module. This source-only check does not prepare
a workspace, create output directories or require a Rust toolchain.

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

Native packages with pinned non-Cargo inputs declare `build.source_archives`:

```json
{
  "type": "native-rust",
  "source_archives": {
    "manifest": "sources.json",
    "cache": "example-sources",
    "cache_env": "HYPER_EXAMPLE_SOURCE_CACHE"
  }
}
```

The manifest is repository-relative and uses the shared source archive format.
The Native driver prepares and validates these inputs only when that repository
appears in the selected Cargo unit graph, including transitive dependencies.
The cache defaults to `OUT/cache/<cache>`; `cache_env` names a `HYPER_` variable
that can override it and is passed to build scripts as an absolute path.
Valid compiled-cache hits do not need to fetch archives again. Cargo and its
build scripts remain offline; network access belongs to driver preparation.

`name` identifies the manifest project; `version` is the description schema version,
not the Rust package version. `tests` and SPDX fields belong to the repository.
Build recipes contain argument arrays, not shell fragments. For example:

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

## Recursive catalogs

The manifest places checkouts; `build/system/component.json` includes the root catalogs.
`build/app/component.json` and `build/svc/component.json` organize their own categories, so
adding a program normally changes its category and the manifest, not build tools.
A catalog can recursively include local fragments and other projects:

```json
{
  "include": [
    "groups/diagnostics.json",
    {"project": "htop"}
  ]
}
```

A string is a path relative to the current description, within the same repository.
An object selects a manifest project and its `component.json`, or an explicit
`file` within that project. Local fragments extend their owner's metadata; an
external project keeps its own configuration, recipes and output namespace.
Lists concatenate, objects merge, and duplicate scalar definitions are errors.
Cycles, duplicate project inclusion, missing projects, obsolete fields and paths
that escape repository ownership are rejected before compilation.

The `app` and `svc` catalog repositories are checked out under `build/`; their
program repositories remain under the workspace's `app/` and `svc/` directories.
The catalogs do not need ignore rules for child program checkouts.

A module with `build.type = "group"` builds its included projects. A group can
organize category files without being a Cargo workspace or owning child sources.

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

Each Rust repository owns its `Cargo.toml`, workspace members and `Cargo.lock`.
`cargo` entries register repository-local manifests and their `system` or `boot`
domain. They do not create a workspace across repositories. Dependencies and
package versions remain Cargo metadata; Native binaries currently use `0.1.0`,
independently of the Native ABI revision. `build.type = "native-rust"` selects the
SDK-backed builder. A library may keep its wrapper in a local workspace member.

Use compatible version ranges for registry dependencies, such as `clap = "4"`,
and omit redundant versions for path dependencies. Exact requirements belong
only to interfaces that actually require an exact version. A repository's
`Cargo.lock` records its selected dependency versions.

Lock ownership stops at shared-library boundaries. A provider's link-probe lock
selects its implementation dependencies; stale transitive copies in an
application lock do not require application commits when that provider changes.
Dependencies reached directly from the application remain application-owned,
even when a shared library also uses them. Composition fails if it would replace
one of those locked versions or require incompatible provider features.

Generated compiler contexts and their locks live only under OUT; no source lock
is rewritten, and there is no build-repository global application lock. Both
editor checks and builds use the installed Native SDK.

### Shared-library feature contract

A shared provider declares its complete feature combination in its own
`component.json`:

```json
{
  "cargo": [{
    "manifest": "Cargo.toml",
    "domain": "system",
    "shared": true,
    "features": ["derive"],
    "default_features": false,
    "link_probe": "tests/link/Cargo.toml"
  }]
}
```

The link probe is an independent, locked executable that consumes the library
with the production compiler profile. All canonical providers must agree on that
profile. A consumer can request a subset of the declared features; an unsupported
request fails and must be addressed in the owning library's contract. Application
feature selection cannot silently expand a shared provider.

The system composes one canonical instance of each shared library, checks the
complete compiler dependency graph, and verifies its bytes after consumer builds.
The compiler context also fixes transitive target and procedural-macro features;
equal top-level feature names alone do not establish Rust dylib compatibility.
Compile-only support crates never enter runtime dependencies or the system image.
Installation follows actual ELF dependencies, so unused providers are not shipped.

Completed Native builds are reused only while their source inputs, configuration,
SDK/compiler identity and recorded artifacts still match. Dependency information
from Cargo's emitted depfiles includes source files outside the primary package.

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
