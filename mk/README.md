<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Native component builds

The root Makefile is the developer entry point. Cargo owns Rust dependency
resolution, CMake owns native compilation, and the component registry owns
delivery metadata. `make app-manifest` prints the expanded registry without
building an SDK or changing any files.

Each Native program and shared library declares delivery in its own
`component.mk`. `mk/components.mk` discovers `app/*/component.mk`,
`bootstrap/stage1/*/component.mk`, `lib/*/component.mk` and
`stdlib/*/component.mk`, then applies the
shared templates under `mk/components/`.
Acceptance-only payloads are declared in `tests/native/component.mk`.

```make
# source directory, Cargo binary, staged filename, destination, image profiles
$(eval $(call native-rust-app,app/echo,hyper-echo,echo,bin/echo,system development))

# source directory, library filename; the template supplies the library layout
$(eval $(call native-rust-shared,lib/clap,libhyper_clap_shared.so))
```

The registry checks each declaration against its Cargo manifest and workspace
membership. Cargo package names are read from those manifests. Dependencies,
features and crate types remain in Cargo; the Make templates do not duplicate
them. Native-only dylib declarations also supply the host-test exclusion list.
Programs under `app/*/` are discovered by Cargo's member glob; app's `.cargo/`
and `target/` are excluded. Add a new program's `Cargo.toml` and `component.mk`
in its directory. Stage-1 programs select their own workspace with `workspace = ".."`; private
libraries below `bootstrap/stage1/lib/` use `workspace = "../.."`. There is no separately maintained deployment
JSON or test library list.

Each package owns its dependencies and features. Keep dependencies used by only
one app in that app's manifest (for example, shell's `shlex` and grep's `regex`).
The workspace supplies shared dependency versions, package metadata, lints and
build profiles. `app/Cargo.lock` pins formal applications and DSOs;
`bootstrap/stage1/Cargo.lock` independently pins bootstrap. Source-only changes
do not require editing either lockfile. Dependency
changes should update only the relevant lock entries, without unrelated upgrades.

`native-file` registers an explicit SDK, configuration or fixture file, with
source, destination, mode and profiles. Sources can use `{apps}`, `{sdk}`,
`{std}` and `{arch}` placeholders. `native-symlink` takes target, destination
and profiles. These declarations are repository-controlled Make source, not an
input format for untrusted manifests.

## Build and composition

`make app` separates static programs owned by `bootstrap/stage1/` from
ordinary applications and shared libraries. The
component registry derives this policy from source ownership. Package-qualified
`APP_FEATURES` and optional probe binaries are routed to their owning graph;
bootstrap features cannot be unified with shared-library consumers. Installation
stages the combined application/library set. Each link graph also separates
components declared with the optional sixth `native-rust-app` argument `core`
from the default `std` components. Freestanding components use the SDK's
`no_std` target directory independently of std feature unification. `make app-check` checks the same component graphs.
The staging directory's `build-manifest.json` records the source revision,
compiler, SDK fingerprints, features and exact artifact hashes and sizes.
Implementation rlibs remain usable by host tests. Native delivery crates select
their shared dependencies explicitly, including the single Rust std owner.
The optional probe binaries are selected with `APP_EXTRA_BINS` and installed
with their `hyper-` prefix removed.

`APP_FEATURES` and `APP_EXTRA_BINS` automatically select a deterministic variant
subdirectory under both `target/app/<arch>/` and `target/app-cargo/<arch>/`.
The empty/default configuration retains the ordinary output paths. Feature and
binary ordering does not affect variant identity. Explicit `APP_OUTPUT` and
`APP_CARGO_OUTPUT` overrides remain available for external test orchestration.
Architecture is part of the path; the SDK's link/std fingerprints and Rust
compiler configuration remain Cargo inputs. Do not mix artifacts from separate
Rust feature graphs, SDKs or compiler builds.

`make app-boot` builds only `APP_BOOT_COMPONENTS` with default Cargo features
disabled and `HYPER_LINK_MODE=static`. The default selection contains init,
console-raw, fallback-sh, io-runtime, fs-backend,
fs-erofs, fs-fat, mount and umount, using their staged names from the same
component registry. The init, shell and I/O runtime packages use implementation
rlibs and have no shared-delivery feature. `fs-fat` is compiled statically and
packaged in boot RAM by default. Omitting it from `APP_BOOT_COMPONENTS` places
it on `/system` instead; neither placement changes which fstab requests a mount.

Bootstrap Cargo artifacts use `APP_BOOT_CARGO_OUTPUT` (by default
`$(APP_CARGO_OUTPUT)/boot-static`) and are staged under `APP_BOOT_OUTPUT`
(`$(APP_OUTPUT)/boot-static`). This separate package selection and target
directory prevent normal workspace builds from enabling shared-library features
in bootstrap executables. Installation checks every selected ELF for the
static-image contract before replacing any staged executable. Debug information
is retained, and the build manifest records static linking and the exact
component selection. Normal `make app` also installs stage-1 components from
the static build tree. The SDK linker rejects dynamic linking
for packages under `bootstrap/stage1/`, independently of the Make entry point;
explicit dynamic builds through `hyper-cargo` fail. Static linking checks the
final ELF as well as the requested flags.

Image composition first applies profile membership and executable overrides.
Rust shared libraries are providers: the packer follows each final ELF's
`DT_NEEDED` and interpreter to add only its transitive dependencies. Providers
come from the staged applications and the selected SDK. Standalone fixtures use
`--library-dir` explicitly. Missing or ambiguous providers fail; host system
libraries are never used as a fallback. Libraries opened through `dlopen` must
be explicit `native-file` entries because they do not appear in `DT_NEEDED`.

The final payload is checked for architecture, SONAME and symbol closure before
publication. Existing symbol policy is preserved: release build artifacts retain
debug information, and packaging runs only `--strip-debug` on temporary copies.
The original ELF files, ordinary symbols and dynamic symbols are retained.

## Boot and system images

Default board builds compose static bootstrap executables directly in the
initial RAM archive root and stage the ordinary executable/DSO closure at
`system-root/`. The archive becomes `/boot` after system init switches root.
The image
packer formats that staging as a dedicated EROFS volume; the configuration FAT
volume remains separate and writable at `/data`. `EROFS_MKFS` selects the host
`mkfs.erofs` executable. The generated image uses uncompressed 4096-byte core
format with xattrs disabled. FAT may be selected for a generated system volume;
its payload must not contain symlinks other than the optional `lib` alias which
the packer can omit.

`BOARD_SYSTEM_LAYOUT=ramfs` puts the same `/system` payload into the bootstrap
archive and creates no system disk volume. Both storage choices use the same
startup and supervision handoff, with separate core/system
service graphs and the system init executable. Image checks validate both
graphs together without pretending that disk executables are in the RAM archive.
The bootstrap loader is the only loader in `/boot`; the formal loader is part
of `/system` alongside the application/library payload. Ramfs validation scopes
both its selected loader and libraries to `system`. The kernel always mounts
the archive at `/`. System init then creates a new ramfs root, moves the original
root to `/boot` and carries the configured mounts across an atomic `switch_root`.
The default formal root contains `/boot`, `/system`, `/data` and `/tmp`, without
root-level aliases. In ramfs deployment the switch also transfers the system
subtree out of the old root, so `/boot/system` does not duplicate `/system`.
These paths remain userspace deployment policy.

System payload hashing checks for concurrent source changes during composition
and stale persistent disks during subsequent builds. The sidecar record is a
build consistency check, not runtime authenticity. Replacing an existing image
remains explicit through `make rebuild` or `make board-rebuild`. Default QEMU
outputs remain under `target/board/qemu/`, and Pi 5 uses
`target/board/rpi5-native/`; ramfs outputs append `-ramfs`. See [boot layout](../docs/boot-layout.md) for deployment policy,
artifacts, recovery behavior and build commands.

## SDK component cache

The SDK builder uses one CMake invocation template for libhyper, dynamic-loader
and userspace-loader. Their installed outputs are cached independently beside
the SDK in `<sdk-output>.components/`, keyed by source/dependency contents,
compiler/tool identities, configuration and relevant environment. Cache hits
verify output contents before restoring them into a fresh staging SDK.
Each loader owns its `elf/` implementation below its source root. Changes to
that mapper invalidate only its owning loader cache.

This cache does not publish SDKs. The existing SDK publisher continues to
serialize writers, check inputs again, preserve unchanged timestamps and replace
the installed SDK only after the entire assembly succeeds. A Rust-source or SDK
version change can therefore reuse unchanged native components. Removing the
cache affects build time only.
