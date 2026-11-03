<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# HypeR build tools

The manifest checks this repository out at `build/system/` inside the source workspace.
The workspace root has no Git repository; repo creates the `Makefile`,
`project.py`, `.vscode` and `.clang-format` links there. It owns
build adapters, composition tools and common development commands. Repository
paths and product descriptions belong to the [manifest](https://github.com/HypeR-Proj/manifest).

```sh
make prepare OUT=$PWD/out
make rebuild OUT=$PWD/out ARCH=aarch64
make -C app/top OUT=$PWD/out
make menuconfig OUT=$PWD/out
make test MODULE=build OUT=$PWD/out
```

`make` and `make rebuild` preserve existing persistent storage. Board builds
refresh the generated `/system` volume without replacing data or guest partitions.
To deliberately recreate the entire board disk, stop its VM and use
`make board-reset-storage` with the same `OUT`, board and configuration arguments.
This explicit reset discards all data in that disk; use a separate `OUT` to keep it.

Ordinary image builds select a compatible, explicitly built I/O VM under
`OUT/build/io-vm` first, then resolve the latest qualified remote artifact.
They never compile Linux implicitly. Local selection checks the platform,
AArch64 image, complete initramfs, content hashes and v5 client-resource contract.
Invalid local output is reported and skipped. If remote selection also fails,
the error prints the explicit build command:

```sh
# Requires the Linux-host prerequisites documented by the io-vm repository.
make iovm OUT=$PWD/out IO_VM_PLATFORM=qemu JOBS=4
make iovm OUT=$PWD/out IO_VM_PLATFORM=rpi5 JOBS=4
```

Non-AArch64 Linux hosts also set `CROSS_COMPILE=aarch64-linux-gnu-`.
See [appliance build prerequisites](https://github.com/HypeR-Proj/io-vm#build).
`IO_VM_LOCAL_BUILD` selects an alternate build-output directory for both the
explicit builder and normal selection. The resolver also recognizes the nested
platform outputs produced by the appliance's `all-platforms` target. A local
build remains selected until rebuilt, its platform index is removed, or an
explicit package/digest is requested; source edits do not implicitly compile it.

`IO_VM_PACKAGE` and immutable `IO_VM_REFERENCE` overrides retain precedence,
including the exact inputs selected by system CI. Without a usable local build,
the remote channel is resolved on each invocation and verified payloads are
reused by digest. There is no silent fallback to an older remote generation.
The fetch script's `--materials` operation selects OCI runtime and corresponding
sources together, independently of local runtime-build selection.

Every normal Make invocation runs preparation automatically. Sources remain in
their repositories; prepared build controls, editor settings, configurations,
caches and images live under `OUT`. Open `out/HypeR-aarch64.code-workspace` in
VS Code after preparing the SDK with `make sdk`.

Each repository declares its build, tests, optional Kconfig and delivered files
in `component.json`. Adding a module updates that repository and the manifest;
it does not require a new hard-coded module list here or in HypeR's documentation.

See [getting started](https://hyper-proj.github.io/HypeR/docs/learn/getting-started.html), [module metadata and outputs](docs/module-builds.md),
[incremental builds](docs/incremental-builds.md), and [Native composition](mk/README.md).
System acceptance is started manually in the [CI repository](https://github.com/HypeR-Proj/ci).

## Persistent image format

New QEMU and Pi 5 deployment images use F2FS for `/data` and EROFS for the formal
system. Pi 5 firmware remains on a separate first FAT partition. Board policies
can declare `boot-files` for that partition and `files` for initial data content.
The F2FS formatter builds the checked-in service core using a host C++20 compiler,
caches it beneath `OUT`, and seeds regular files/directories without mounting it.
F2FS volumes require at least 128 MiB; the service must remain selected in the
system configuration. Custom FAT data volumes remain supported.

Ordinary `make`, `rebuild` and `board-build` preserve data. Existing QEMU FAT disks
with unchanged geometry continue to mount through format detection; they are
not converted. The new Pi 5 layout changes GPT geometry, so an old disk is
rejected before refresh. Back up data and create a new image in a separate output
directory. `board-reset-storage` explicitly replaces **all** disk contents.
F2FS read-only recovery mounts are currently rejected; bootstrap recovery can
read the EROFS system volume but has no F2FS worker.
