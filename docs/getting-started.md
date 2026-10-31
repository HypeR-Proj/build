<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Getting started

[Build tools](../README.md)

First create a repo-managed workspace:

```sh
mkdir workspace && cd workspace
repo init -u https://github.com/HypeR-Proj/manifest -b main
repo sync
make prepare
```

Install the Android `repo` tool before initializing the workspace. All build commands below
run from the workspace root; each component checkout remains independently editable.

## Prerequisites

- Rust and rustup; `rust-toolchain.toml` selects the pinned compiler and
  components;
- Clang/LLVM;
- GNU Make;
- Python 3.11+ for SDK source preparation and workspace contract checks;
- QEMU for the selected architecture; AArch64 uses `-cpu max` by default and
  requires FEAT_VHE (hardware without it is unsupported);
- `curl`, `cpio`, `gzip`, `tar`, and SHA-256 tooling for the Linux guest assets;
- dosfstools and mtools for the board FAT image; erofs-utils (`mkfs.erofs`) for
  the default read-only system volume; e2fsprogs and squashfs-tools for the
  Alpine root disk;
- ORAS for digest-pinned I/O appliance downloads: the importer uses `PATH` or
  downloads a checksum-pinned ORAS 1.3.0 automatically on supported hosts.
  `IO_VM_ORAS` can select an executable; verified packages are reused offline;
- `dtc` when building the x86-64 QEMU platform description.

### macOS with MacPorts

MacPorts' `erofs-utils` requires `libuuid`, while its `e2fsprogs` requires the
conflicting `ossp-uuid`. Keep `erofs-utils` installed and let the build provide
private ext4 tools instead of installing the `e2fsprogs` port:

```sh
sudo port install erofs-utils dosfstools mtools squashfs-tools
make host-e2fsprogs
```

`make host-e2fsprogs` downloads SHA-256-pinned e2fsprogs 1.47.3 and compiles only
`mke2fs`, `debugfs` and their static support libraries under `OUT/build/build/host-tools/`.
It needs a host C compiler, Make, curl and Python 3.11+; no root access or
Autotools installation is needed. The image packer also invokes this fallback
automatically when it cannot find the ext4 tools in the system or Homebrew.
Subsequent builds reuse the local tools offline. `make clean` removes them;
`HOST_CC` selects the compiler for the explicit preparation target.

Ensure `python3 --version` reports Python 3.11 or newer. With MacPorts Python
3.14, for example, `sudo port select --set python3 python314` selects it for
the build; Apple's `/usr/bin/python3` may be too old.

Build the AArch64 kernel, Native SDK, and initramfs, then run the complete
system with the default boot/system separation:

```sh
make defconfig
make
make run
```

The build imports the matching protocol version 3 appliance from
[the qualified platform channel](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html#protocol-version-3-appliance-requirement), verifies its immutable
GHCR digest and caches it locally. See [appliance requirements](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html#protocol-version-3-appliance-requirement)
for the package boundary and default QEMU deployment.
`make run` uses the already packaged appliance and needs no package argument.

The kernel's early boot log includes its crate version, Git revision and UTC
build time, for example
`HypeR version 0.1.0-0123456789ab-dirty (built 2026-10-06T00:00:00Z)`.
Uncommitted changes and untracked source files add the
`-dirty` suffix; an exported source archive retains that revision in its source inventory.
Archives without either Git metadata or an inventory report `unknown`.
Incremental `make` builds refresh the identity when source state changes and
retain the timestamp when the kernel is not recompiled. `SOURCE_DATE_EPOCH`
can supply a fixed Unix timestamp for reproducible builds. The I/O VM prints
its own appliance revision and build time independently of Linux's release.

`make` builds the Rust std-based init, direction-attenuated Console workers,
session manager, capability-scoped shell, VM manager, and isolated VM runtime
only through the assembled SDK under `OUT/sdk/aarch64`. It also downloads
the checksum-pinned AArch64 Linux inputs and, on first build, packages the guest
FIT and root disk into the board disk. Later builds preserve that disk,
including its VM definitions, Alpine FIT and root filesystem. Changes to
the manifest-owned QEMU board definition or the guest `/init` do not update those existing files.
`make run` only starts existing artifacts. A changed system executable or
library requires a matching system image; the build rejects a stale system
payload instead of silently pairing it with a new bootstrap. `make rebuild`
repacks the whole disk and resets its data. See
[updating existing deployments](https://hyper-proj.github.io/build/docs/deployment/board-storage.html#updating-existing-deployments)
before changing a persistent deployment.

The applications do not include private kernel or SDK source paths. The RAM
archive initially supplies `/init`, `/bin`, `/svc` and the bootstrap loader.
Stage-1 init starts storage, mounts the independent system volume temporarily
at `/system`, switches it to `/`, then launches system init. The original archive
is retained at `/boot` until formal providers take over and bootstrap retires.
Ordinary programs and shared libraries live on the EROFS root; `/tmp` is RAM and
`/data` is the persistent FAT volume.
Ordinary applications are dynamic PIEs by default and share `libsystem.so`;
SDK consumers can select a self-contained static PIE with
`HYPER_LINK_MODE=static`. Both kinds of system executable use the explicitly
delegated formal loader; only bootstrap programs use the static bootstrap
loader. See [boot and system deployment](https://hyper-proj.github.io/bootstrap/docs/boot-layout.html) for the launch contract.
Pass `INITRAMFS=/path/to/archive.cpio` to test another Native userspace image.

The default AArch64 board profile retains the HypeR shell and starts a resident
Linux I/O VM with its assigned QEMU virtio-scsi disk and virtio-net uplink. Its
Native owner is `io-runtime`, visible in `ps`. It mounts the configuration
volume at `/data` and provides disk and network backends to configured guest VMs. Linux creates
the bridge and connects each guest TAP automatically; QEMU user networking
supplies DHCP and NAT. After `vmm start alpine`, use `vmm console alpine` to
enter the guest. Alpine starts DHCP in the background and renews its lease;
an absent DHCP server does not block its shell. The persistent disk is
`OUT/build/build/board/qemu/disk.img`; `BOARD_IMAGE` selects another board disk. Formal
programs are not embedded in the initial archive. For a diskless Pi 5 diagnostic,
`make rpi5-bringup` builds only static stage-1 and its fallback shell.

For an idle-backend diagnostic without mounted Native storage, build with
`make RUN_PROFILE=io`,
then launch with `make run RUN_PROFILE=io`. Its separate
64 MiB disk is created once during the build. Set `IO_VM_DISK=/path/to/disk.img`
on both commands to select another diagnostic disk. See [I/O VM](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html).

Native-only tests use an explicit fixture init that copies the formal payload
into an independent RAMFS before entering the ordinary stage-1 supervisor.
This keeps no-device and secondary-architecture tests separate from product
deployment. These fixtures do not measure block storage or boot latency;
`test-boot-system` separately verifies the real EROFS-backed handoff. A product
bootstrap archive alone provides only its static recovery environment.

Run the standalone kernel mechanism tests separately:

```sh
make test-qemu ARCH=aarch64
```

This explicitly enables `kernel-test,kernel-self-test` and starts a four-CPU QEMU
`virt` machine. Kernel self-tests do not load Linux or create a default VM.
Use `make test-native` for userspace-managed Linux guest integration. Its guest
downloads are cached under the platform temporary directory and generated
payloads remain under `OUT/build/kernel/guest/`.

Select another architecture explicitly:

```sh
make -C kernel defconfig ARCH=riscv64
make test-qemu ARCH=riscv64

make -C kernel defconfig ARCH=x86_64
make image ARCH=x86_64
make test-image ARCH=x86_64
```

The default x86-64 QEMU configuration uses TCG, which cannot execute VMX.
Hardware-assisted guest execution requires a suitable KVM/nested virtualization
environment and is not yet a public CI contract.

Useful targets:

| Command | Purpose |
| --- | --- |
| `make image ARCH=<arch>` | Build the canonical ELF and delivery image |
| `make sdk` | Assemble the AArch64 Native SDK under `OUT/sdk/aarch64` |
| `make sdk-check` / `make sdk-test` | Verify SDK generation, publication, compilation, and portable runtime behavior |
| `make native-initramfs` | Build the test-only RAMFS handoff fixture; product images use independent system storage |
| `make` / `make run ARCH=aarch64` | Build while preserving persistent disk contents / start existing artifacts |
| `make rebuild` | Repack the board disk, resetting persistent contents |
| `make test-native` | Verify the Native service graph, command execution, and managed Linux VM console attach/detach under QEMU |
| `make guest-assets ARCH=<arch>` | Download and package the pinned Linux guest inputs |
| `make check ARCH=<arch>` | Run target checks and Clippy, including kernel self-test builds |
| `make test ARCH=<arch>` | Run kernel host, Kconfig, and kallsyms tests |
| `make test-image ARCH=<arch>` | Verify the ELF, relocation, image, and architecture contract |
| `make test-qemu ARCH=<arch>` | Run the architecture's QEMU acceptance test where supported |
| `make release ARCH=<arch>` | Build and verify a stripped copy of the canonical kernel image |
| `sh tests/ci/run.sh <suite>` | Run the same named suite or serial aggregate as CI; see the [suite catalog](https://hyper-proj.github.io/system-tests/) |

The old `verify`, `verify-runtime`, `verify-image`, `verify-boot`, `verify-smp`,
`check-all`, `test-all` and `verify-all` Make targets have been removed. Use
`make image` followed by `make test-image` for image validation, `make test-qemu`
for kernel runtime tests, and the named CI suites for combined checks. The system-tests repository owns suite composition; Make owns individual builds and fixtures.
Run suites serially within one checkout because feature variants share output
paths.

The default AArch64 image is written to:

```text
out/build/kernel/aarch64-unknown-none-softfloat/kernel/hyper
out/build/kernel/aarch64-unknown-none-softfloat/kernel/hyper.img
```

`make release` strips debugger-only sections from the canonical ELF without
recompiling it, then verifies that the resulting raw image is byte-identical.

`make native-initramfs` copies its payloads into a temporary staging directory
and runs `llvm-strip --strip-debug` on the ELF copies before packaging. Packaged
applications and libraries retain their symbol tables; original application and
SDK build products retain all debug information. Set `LLVM_STRIP` to override
the tool path.

See [Native applications](https://hyper-proj.github.io/HypeR/docs/usage/applications/README.html) for file tools, process monitoring,
named VM commands, and `/data/vms.json` board configuration.

## Configuration

HypeR uses an in-tree, dependency-free Kconfig-like tool. It reads
`kernel/Kconfig`, writes `OUT/config/<arch>/modules/kernel.config`, validates dependencies and ranges,
and selects optional Cargo features. Other declared symbols are exported as
checked Rust `cfg` values and typed constants.

```sh
make menuconfig                 # product and independently scoped module menus
make defconfig                  # restore product and kernel architecture defaults
make olddefconfig               # migrate the product .config
make -C kernel olddefconfig      # migrate the kernel module configuration
make -C kernel defconfig         # restore only kernel architecture defaults
```

`CONFIG_FILE` selects an alternate complete configuration without replacing a
saved kernel configuration:

```sh
make image ARCH=aarch64 CONFIG_FILE=kernel/configs/qemu_aarch64_defconfig
```

See [Incremental builds](https://hyper-proj.github.io/build/docs/incremental-builds.html) for cache behavior and recovery.

For physical Pi 5 bring-up, see the [official firmware boot and debug UART setup](https://github.com/HypeR-Proj/kernel/blob/main/docs/usage/rpi5.md).
