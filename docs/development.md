<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Development guide

[Project overview](https://hyper-proj.github.io/HypeR/index.html)

## Rust editor support

Open the repository root in VS Code with the `rust-lang.rust-analyzer`
extension installed. The shared `.vscode/settings.json` explicitly loads all
Cargo project roots, including the stdlib workspace, bootstrap, Native applications,
shared libraries under `lib/`, Native acceptance applications and build tools.
The shared libraries and `tests/app/` programs belong to
`app/Cargo.toml`; do not add them as separate linked projects. New formal services
under `svc/`, including component-manager, I/O and filesystem services, are discovered by that
workspace's member glob. The standalone `tests/native/io-smoke` fixture has its
own linked project and static Native configuration. Run
`make sdk ARCH=aarch64` once to prepare the Native target
and patched std sources, then **Developer: Reload Window** after updating editor
settings. Continue opening
the repository root; no separate editor workspace or generated configuration
is needed.

Reload the VS Code window, not just the language server, if new settings are
ignored. On some mounted volumes VS Code refuses to watch `.vscode` for file
changes; restarting rust-analyzer then reuses the cached client configuration.
The extension's Output log should show the configured `cargo.extraEnv` and
`check.overrideCommand`, rather than `{}` and `null`.

The editor-only `.vscode/rust-analyzer.toml` supplies the AArch64 kernel
configuration, without dependency patches. Applications and the smoke programs
keep SDK source patches and editor targets in their workspace-local Cargo
configuration. This prevents editor discovery from adding `patch.unused` records
to kernel, ABI and host-tool lockfiles that ordinary builds would remove.
The production `hyper-cargo` driver overrides source paths with installed SDK
paths; save-time editor checks explicitly select the local configuration.
The freestanding Rust smoke program selects `aarch64-unknown-none`;
kernel and host projects retain their configured targets. The shared std source
tree contains both HypeR and upstream host implementations; each project selects
its platform through the actual target cfgs. This resolves `File::into_std()`
and `std::os::hyper` without pretending that host code targets HypeR.

Build-script discovery and save-time diagnostics both use
`scripts/rust-analyzer-check.sh`: Native projects use the existing `hyper-cargo`
driver with repository SDK source patches, while kernel/host projects keep
ordinary Cargo checks including test targets. Editor artifacts are separate
from production outputs under `target/rust-analyzer`. All configured paths are
repository-relative or resolved at invocation time, so relocating the checkout
does not require regenerating settings. Root Make commands still explicitly
select their production architecture and do not load the app-local Cargo config.

Both editor commands explicitly run once per Cargo workspace. The check script
resolves the owning workspace with `cargo locate-project` before selecting its
target and SDK configuration, so invoking it from `lib/tool-args`, a `shared/`
crate, or an application subdirectory uses the same Native configuration as
`app/`. The same resolution applies from `tests/app/terminal-probe`,
`tests/app/vm-smoke` and `tests/app/fs-failure`, including their `src/` directories.
Bootstrap subdirectories resolve to `bootstrap/stage1/` and select
static linking, including checks invoked from the source-only `contracts/`
directory and an executable's embedded parser modules. Those modules are indexed
through the consuming package, not separately listed as Cargo projects. Formal
workspaces explicitly select dynamic linking, so an inherited `HYPER_LINK_MODE`
cannot change their editor graph. `tests/native/io-smoke` uses its own static workspace.
A direct invocation from the repository root checks `app/`.
Checks use `--locked` so editor activity cannot silently rewrite lockfiles.

The editor defaults to AArch64, like the existing kernel editor configuration;
this does not change RISC-V builds. Rerun `make sdk ARCH=aarch64` and restart
rust-analyzer after changing std/toolchain sources. Other LSP clients can reuse
the settings in `.vscode/settings.json`, with the `rust-analyzer.` prefix removed
and the repository root as their root directory. When adding an independent
Cargo project, add its manifest to `linkedProjects`; kernel and SDK workspace
members are discovered automatically. The kernel workspace includes the binary,
`hyper-core`, `hyper-platform`, `hyper-drivers`, `hyper-vm-models`, `hyper-hal`,
and `hyper-trace`.
Only `kernel/Cargo.toml` belongs in `linkedProjects` for those seven crates;
adding their manifests separately duplicates workspace discovery. Module moves
within a crate are discovered through Rust module declarations, including
directory-local `mod.rs` entry points, and need no editor path mapping.

## Testing and CI

Use these repository-root commands for short feedback loops:

| Change | First check |
| --- | --- |
| App parser, state machine, or policy library | `make app-test APP_TEST_ARGS='test_name'` |
| Native ABI and SDK source behavior | `make sdk-test` |
| Installed SDK consumption by apps | `make app-sdk-test ARCH=aarch64` |
| Portable kernel mechanisms | `make -C kernel test ARCH=aarch64` |
| App deployment and packaging | `python3 project.py test build` |
| Kernel or app runtime behavior | The relevant QEMU target below |
| Product trimming and kernel test gates | `python3 project.py test build` and `KERNEL_CI_SUITE=configurations python3 project.py test kernel` |

`app-test` uses SDK source patches within both Native workspaces, an explicit
host target, and ordinary Cargo. It does not assemble an SDK or require patched
std; the first invocation can download missing Cargo dependencies.
`APP_TEST_PACKAGE=hyper-component-manager` selects the formal supervisor;
`APP_TEST_WORKSPACE=bootstrap/stage1 APP_TEST_PACKAGE=hyper-bootstrap-stage1-init`
selects the independent bootstrap supervisor;
`APP_TEST_ARGS=supervision` filters its test names.
Run it from the repository root so Native editor-target defaults are not loaded.
`app-sdk-test` separately tests the assembled SDK contract. Production app
manifests and builds continue to consume installed SDK sources.

`make app` selects `APP_BUILD_PROFILE=system`: it builds static programs from
`bootstrap/stage1/`, including the I/O runtime, then ordinary system applications
and shared libraries from `lib/`, excluding development-only probes. These libraries explicitly belong
to `app/Cargo.toml`; they use the same installed-SDK boundary, lints, checks and
host tests as applications. Native consumers select the `shared/` dylib packages;
host tests use the implementation rlibs. The Native-only delivery packages are
excluded from host test linking. `make app-fixtures` selects the development build profile and additionally builds
static/dynamic linking probes
and Rust std smoke programs. Specialized VM smoke binaries remain owned by their
acceptance targets.

Use `make menuconfig` to select a smaller product without editing the component
registry. Its saved `.config` controls both compilation and deployment; selecting only an image
profile is no longer the only way to remove applications or filesystem workers.
The [configuration guide](https://hyper-proj.github.io/manifest/docs/configuration.html) covers domain Kconfig definitions, executable
dependencies, kernel features and independent output directories.

Feature-specific fixtures build the selected component graph and stage its
executables and DSOs together. `APP_FEATURES` selects package-qualified Cargo
features; `APP_EXTRA_BINS` selects additional acceptance binaries. Both select
an automatic variant subdirectory for the Cargo cache and staged applications.
An explicit `APP_OUTPUT` can name a fixture's own staging directory. Updating
only a runtime binary can leave its Rust generic symbols incompatible with
previously staged libraries, even when their SONAMEs match.

[Component declarations and templates](https://github.com/HypeR-Proj/build/blob/main/mk/README.md) own installation and
image membership. Each app or library declares its delivery in `component.json`;
[the registry](https://github.com/HypeR-Proj/build/blob/main/mk/components.mk) expands those declarations and checks their
Cargo targets. `make app-manifest` prints the resulting installation manifest.
Cargo workspace membership is generated from module metadata; package dependencies remain in Cargo. Service manifests
still own startup and capabilities, and board JSON owns storage and device
deployment. The registry also determines which delivery dylibs to exclude from
host test linking.

Staged Rust library providers use `lib64/<arch>-hyper-hyper/`; deployment
places the selected closure in the independent system filesystem.
Packaging collects their transitive `DT_NEEDED` closure after applying executable
overrides. Additional standalone fixtures supply `--library-dir` instead of
manually listing Rust libraries. Runtime-loaded plugins remain explicit entries.
The component registry also declares loader payloads; the composer places the
bootstrap loader at `lib64/` in the initial RAM archive and the formal loader
in the system payload's `lib64/`. The former becomes `/boot/lib64` after
stage1 switches the root namespace.

`NATIVE_IMAGE_PROFILE=development` is the default for the explicit Native test
fixture and selects its apps and test programs; product board images use the
system profile. `NATIVE_IMAGE_PROFILE=system` includes all ordinary apps
and runtime libraries without acceptance probes; it also avoids building those
probes. The I/O service is included by the existing I/O/board boot profiles.
Both image profiles retain the same stage-1/system-init handoff. Image profile
selection controls payload membership, not the startup architecture. For
AArch64, formal programs use:

```text
/lib64/ld-hyper-aarch64.so
/lib64/aarch64-hyper-hyper/
    libsystem.so
    libhyper_rust_std.so
    libhyper_clap_shared.so
    libhyper_tool_args_shared.so
    libhyper_vm_policy_shared.so
    libhyper_vm_support_shared.so
```

RISC-V uses `riscv64-hyper-hyper` and `ld-hyper-riscv64.so`. ELF `PT_INTERP`
names `/lib64/ld-hyper-<arch>.so`. Launchers pass an explicit executable loader
File and the selected `/lib64/<arch>-hyper-hyper` Directory capability.
The kernel does not resolve the interpreter string or choose the library
directory. The formal loader opens exact `DT_NEEDED` names relative to the
delegated directory, with no fallback to another architecture or an ambient
search path. Static system executables also use this loader; stage-1 services
use the separate bootstrap loader. The [deployment guide](https://hyper-proj.github.io/bootstrap/docs/boot-layout.html)
describes the root switch, separate boot/system environments and supervision handoff.
This runtime directory name does not change the Rust `<arch>-unknown-hyper`
compiler target. Architecture-specific SDK and application build staging areas
retain their own `lib/` artifact directories for link libraries; the SDK stages
the interpreter in `lib64/`. Deployment selects the runtime library paths.

Before publication, packaging validates the selected ELF dependency graphs,
architectures, SONAMEs and dynamic symbols. It copies ELF files before stripping
only debug information and
preserves unchanged output timestamps. Use a separate `NATIVE_INITRAMFS` or
`BOARD_OUTPUT` when comparing profiles.

Archive entry triples normally contain `MODE ARCHIVE_PATH SOURCE`. For a symbolic
link use `symlink ARCHIVE_PATH NAMESPACE_TARGET`; targets can be absolute or
relative paths, without empty, `.` or `..` components. The packer emits a symlink
with mode 0777
and stores the target text, without following build-host paths. Files must be
listed under their real directories rather than beneath an archive symlink.

Before packing, bootstrap manifests and individual formal service/target units
are checked on the host using their owning supervisor's production
parser, bootstrap authority policy, and supervision rules. Invalid syntax,
dependencies, capability declarations, unsupported restart policies, or missing
service images reject the build without replacing the existing ramdisk. The
check also runs before an incremental packaging cache hit.
For a standalone configuration check (without image membership checks), run:

```sh
python3 scripts/check-service-manifest.py bootstrap/stage1/init/config/services-with-vms.json
```

This uses the source workspace and host Rust toolchain; it does not need a built
HypeR SDK. Live handle availability and kernel authorization remain boot-time
checks. `python3 -B tests/build/service-manifest.py` runs the host regression
suite, also included in Native CI.

Plain `make` (or `make all`) builds the kernel and the selected profile's ramdisk.
The default AArch64 board profile creates a disk only if missing; subsequent
builds preserve `/data` and guest disks while updating the host-side kernel and
bootstrap loaded by QEMU and refreshing the generated EROFS system volume.
The refresh validates the existing GPT and stages a complete image, preserving
all bytes outside the system partition, before atomic publication. Stop QEMU before updating
its system volume. Changed partition layouts still require an explicit rebuild
or a fresh output directory; ordinary builds never repartition existing data.
`make run` only starts existing artifacts and fails if
any are missing; it never builds or packages them.

`make rebuild` (or `make board-rebuild` for the board profile) repacks the full
board disk, resetting persistent contents after successful packing. Use
`make clean && make rebuild` to compile from a clean build tree as well.
`make clean` deletes all of `target/`, including default persistent disks;
disks that must survive cleaning need an external `BOARD_IMAGE` or `IO_VM_DISK`.
`make board-image` still refuses an existing output unless explicitly replaced.
For Native/standby profiles, select the same `RUN_PROFILE` for build and run.

Automatic module workflows own source quality, architecture builds, image
contracts and SDK consumers. Manual system verification owns runtime acceptance. The AArch64 matrix exercises
VHE with different address-space geometries, UP/SMP, kernel self-tests, and
virtual interrupts and timers. A separate negative boot test verifies that
CPUs without FEAT_VHE reach the explicit rejection loop. Linux guest startup is
covered by Native userspace integration through the VMM tools. RISC-V Native integration runs the
same static/dynamic std, process, thread, filesystem, shell and userspace Linux
VM contracts on one and four harts. A separate `make test-vm-smoke ARCH=riscv64`
init fixture validates Native VM lifecycle, timer/serial WFI wakeups and
retirement independently of Linux loading.
The no-device Native fixtures use a test-only init to copy their formal payload
into an independent RAMFS, then run the production stage-1 supervisor and
system init. This preserves userspace coverage on secondary architectures
without an embedded-system product mode. It does not validate block storage or
boot latency; the separate AArch64 `test-boot-system` gate validates the real
EROFS-backed root and bootstrap retirement.
Linux acceptance explicitly attaches `/bin/vmm` to the buffered guest serial port, exercises guest-console RX, and detaches
through the local Ctrl-] menu. Both Native
integration suites also verify named VM isolation and repeated runtime crashes
with guest-memory reclamation. x86-64 has a build and image contract only.

Stable local equivalents live in `tests/ci/run.sh`:

```sh
python3 project.py test build
KERNEL_CI_SUITE=quality python3 project.py test kernel
sh tests/ci/run.sh native                 # all AArch64 Native shards, serially
sh tests/ci/run.sh native-console         # one independent CI shard
sh tests/ci/run.sh native-gicv3            # serial console + VM aggregate
sh tests/ci/run.sh native-stack-16k        # minimum-stack Native boot on one/four CPUs
sh tests/ci/run.sh board-storage
sh tests/ci/run.sh io-vm
sh tests/ci/run.sh riscv64-runtime
sh kernel/tests/ci/run.sh aarch64-build
QEMU_CPU=max QEMU_CPUS=4 sh kernel/tests/ci/run.sh aarch64-qemu
sh kernel/tests/ci/run.sh riscv64-qemu
sh kernel/tests/ci/run.sh x86_64-build
```

QEMU tests track and terminate the processes they create. Failed CI runs retain
serial logs as artifacts. See [the CI contract](https://github.com/HypeR-Proj/system-tests/blob/main/ci/README.md) for the
exact coverage and supported runtime expectations.

## Repository guide

| Path | Responsibility |
| --- | --- |
| `kernel/` | Independently buildable hypervisor kernel, configuration, documentation, tests, and build tooling |
| `kernel/core/src/` | Foundation types, synchronization, containers, memory mechanisms and portable HAL contracts |
| `kernel/platform/src/` | Firmware parsing and platform resource descriptions |
| `kernel/drivers/src/` | Physical devices and firmware-interface drivers |
| `kernel/vm-models/src/` | Guest-visible device models and portable virtualization contracts |
| `kernel/hal/src/` | Public machine capabilities and private architecture implementations |
| `kernel/trace/src/` | Bounded event recording and tracepoint macros |
| `kernel/src/` | Runtime ownership, policy, scheduling, IRQ, memory, devices and VM orchestration |
| `kernel/src/diagnostics/` | Logging, crash handling, inspection, symbols, profiling and trace control |
| `kernel/src/fs/` | Filesystem primitives, archive import, VFS and file-cache residency |
| `kernel/build_support/` | Shared build-time configuration parsing |
| `sdk/abi/` | Native ABI schema, generated Rust definitions, C header, and reference |
| `stdlib/system/` | Freestanding Native C runtime and architecture syscall veneers |
| `svc/` | Formal background services, root component-manager, system-startup, journal and retirement |
| `bootstrap/stage1/` | Static bootstrap services, recovery tools and initial executable loader |
| `lib/dynamic-loader/elf/` | Shared bounded ELF mapping and initial stack construction |
| `lib/dynamic-loader/` | Formal static/dynamic application loading, relocation and runtime loading |
| `stdlib/rust/` | Safe `no_std` Native OS binding, raw syscalls, and Rust runtime entry |
| `sdk/toolchain/` | Clang/Cargo drivers, linker contract, and transactional SDK assembly |
| `app/` | Native system applications built only against the assembled SDK |
| `kernel/tests/` | Kernel host tests, self-tests, image verification, and QEMU acceptance |
| `kernel/tools/` | Kconfig, kallsyms, and Linux guest-payload tooling |
| `tests/` | Repository-wide contracts and Kernel/SDK/application integration tests |
| `tools/` | Product-composition tooling shared across source domains |

Further documentation:

- [Kernel stack budgets and workload checks](https://github.com/HypeR-Proj/kernel/blob/main/docs/usage/stack-budgets.md)
- [Architecture boundaries](https://github.com/HypeR-Proj/kernel/blob/main/docs/architecture/README.md)
- [Native init contract](https://github.com/HypeR-Proj/kernel/blob/main/docs/design/native-init.md)
- [Userspace and syscall architecture](https://github.com/HypeR-Proj/kernel/blob/main/docs/design/syscall-abi.md)
- [Virtual filesystem architecture](https://github.com/HypeR-Proj/kernel/blob/main/docs/design/vfs.md)
- [Native SDK contract](https://github.com/HypeR-Proj/build/blob/main/sdk/README.md)
- [HypeR Native ABI reference](https://github.com/HypeR-Proj/abi/blob/main/docs/native.md)
- [VM image format and boot ownership](https://github.com/HypeR-Proj/kernel/blob/main/docs/design/vm-bundle.md)
- [Linux I/O VM ownership and package delivery](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html)
- [RISC-V execution profile](https://github.com/HypeR-Proj/kernel/blob/main/docs/architecture/riscv64.md)
- [x86-64 execution profile](https://github.com/HypeR-Proj/kernel/blob/main/docs/architecture/x86_64.md)
- [Crash console](https://github.com/HypeR-Proj/kernel/blob/main/docs/usage/crash-console.md)
- [Security policy](https://hyper-proj.github.io/HypeR/SECURITY.html)
- [External guest payload and licensing](https://github.com/HypeR-Proj/kernel/blob/main/tools/guest/README.md)

## Contributing

HypeR welcomes contributions in architecture support, virtualization, memory
management, drivers, testing, documentation, and design review. The project is
still establishing core contracts, so structural changes should begin with the
invariant they enforce and the dependency direction they preserve—not only a
file move or a new abstraction.

Before opening a pull request:

1. Keep source code, comments, documentation, diagnostics, and commit messages
   in English.
2. Avoid `unwrap`, `expect`, implicit panic paths, and unnecessary unsafe code.
3. Document ownership, publication, synchronization, and hardware/ABI
   obligations that are not evident from the types.
4. Add host tests for portable mechanisms and architecture acceptance coverage
   for changed low-level paths.
5. Run `git diff --check`, `KERNEL_CI_SUITE=quality python3 project.py test kernel`,
   `python3 project.py test build`, and `make check` for every affected
   architecture. Run the relevant QEMU contract when runtime behavior changes.
6. Do not add GPL-derived implementation code. New dependencies require
   explicit `no_std`, maintenance, and license review.
7. Preserve the SPDX copyright and Apache-2.0 header in every new tracked text
   file. Cargo-generated lockfiles and the complete `LICENSE` text are exempt.

Small, coherent changes with a clear migration seam are preferred over broad
rewrites. [Open an issue](https://github.com/roolrz/HypeR/issues) before
starting work that changes a public format, architecture boundary, unsafe
ownership model, or guest-visible ABI. Issues are also the preferred place for
bug reports and focused design proposals.

## Guest artifacts and licensing

HypeR source code is licensed under the [Apache License 2.0](https://github.com/HypeR-Proj/HypeR/blob/main/LICENSE).

`make guest-assets` downloads external Linux and Alpine artifacts for testing.
They are checksum-verified, ignored by Git, and are not part of the
Apache-2.0-licensed source distribution. Linux is GPL-2.0-only and Alpine
packages carry their own licenses. Anyone redistributing generated guest
payloads must preserve the relevant upstream notices and source-availability
obligations. See [kernel/tools/guest/README.md](https://github.com/HypeR-Proj/kernel/blob/main/tools/guest/README.md) for details.

The Linux I/O appliance has a separate delivery path: HypeR-io-vm builds and
publishes the complete kernel/initramfs as a GHCR package, while HypeR imports
an immutable digest and supplies its own DTS/DTB and launch policy. Its Linux
sources, configuration, modules, services and rootfs assembly live in that
repository. Corresponding source materials travel with the published package;
they are not added to the HypeR boot ramdisk. See the
[I/O VM contract](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html) for ownership, import instructions and current limits.

## Native std incremental builds

The assembled SDK records a content fingerprint for its Rust standard-library
sources and Native target descriptions. `hyper-cargo` includes this identity in
std build flags, so changes to the installed std port invalidate cached
`build-std` artifacts in both check and release profiles. Unchanged content
retains the same identity regardless of file timestamps; app-only edits do not
invalidate std. Native libraries and linker tools retain their separate link
fingerprint.

New project-authored files must carry an `SPDX-FileCopyrightText` declaration
with the contributor or copyright holder and a year (or year range), together
with `SPDX-License-Identifier: Apache-2.0`. Preserve existing and upstream
attribution; the header check does not require the repository owner's name.

### Dependency maintenance

Dependabot proposes immutable Action updates and Cargo updates for the kernel,
its host tests/kallsyms tool, Rust SDK and FIT tool. Dependency-free tool roots
need no registry updates. Application and SDK smoke workspaces intentionally
consume unpublished, installed SDK crates: their updates remain manual using
the normal SDK preparation and consumer checks, rather than committing a source
patch overlay into their production manifests. The repository owner reviews
these updates and new external inputs.

`sh scripts/audit-dependencies.sh` runs cargo-audit 0.22.2 against every tracked
Cargo lockfile, including those consumers. CI runs it on lockfile changes and
weekly so newly published advisories are noticed without a code change. It checks
known RustSec advisories, not reachability or non-Rust appliance packages. Any
future advisory exception must name its ID, applicability rationale, reviewer
and review date in `.cargo/audit.toml`; no blanket advisory suppression is used.

The SPDX source check preserves project and vendored notices. New dependency
licenses still require owner review of the actual license text and distribution
obligations; passing an advisory scan is not license approval. External Linux
appliance and firmware distribution retain their separate provenance and
corresponding-source requirements.

The root Makefile retains shared defaults and command orchestration; component
recipes live in `mk/sdk-app.mk`, `tests/mk/native.mk` and `mk/boards.mk`. The
console, application and runtime-crash QEMU tests share subprocess ownership,
serial buffering and deadlines through `test-framework/python/hyper_test/session.py`; their scenario
assertions stay in each test. Editor metadata and save-time checks use Native
workspace-local SDK patches; the shared configuration contains no dependency
patches. Kernel and host-tool lockfiles therefore do not acquire Native
`patch.unused` records through the editor configuration.

Host-test workspaces with ignored lockfiles are compiled and tested separately;
the tracked-lock audit does not assert their exact independently resolved graph.

For focused maintenance feedback, `make app-test APP_TEST_PACKAGE=hyper-vm-manager`
executes the same fleet decisions used by the service, with explicit event time
and recorded resource effects. `make app-test APP_TEST_PACKAGE=hyper-vm-support`
checks the shared image/device/protocol mechanisms. `make sdk-test` includes
C/Rust transport capture tests; `sh sdk/toolchain/scripts/check-dynamic-loader.sh`
executes the production loader's parser, relocator and rollback paths against
host fixtures. `sh sdk/toolchain/scripts/check-userspace-loader.sh` checks the
executable mapper, startup-vector handoff and early self-relocator for both
architectures. Both scripts use UBSan on all hosts and ASan additionally on
Linux. C runtime tests also cover final-stack retirement and loader-result
handling. These checks
complement, rather than replace, Native dynamic-linking and cross-VM acceptance.
