<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# HypeR Native applications

Build and deployment options are declared in this directory's [Kconfig](Kconfig).
Use the repository's `make menuconfig` and `.config` to select programs; see
[product configuration](../docs/configuration.md) for dependencies and defconfig workflows.

This directory contains foreground Native tools. Background programs live in
[`svc/`](../svc/README.md), with independent service policy and logging.
The RAM-resident boot and recovery programs live in
[`bootstrap/stage1`](../bootstrap/stage1/README.md), an independent static-link
workspace with its own lockfile and executable-owned implementations. The workspace here owns
formal applications and reusable libraries in the top-level `lib/`.
Native acceptance executables live under [`tests/app`](../tests/app/README.md);
they share this workspace's dependency lock but are built or packaged only by
their selected test targets or development profile.
Target builds consume the assembled
Native SDK; source-level host tests use workspace SDK patches. Applications
do not reach into kernel or runtime implementation internals.

VM management remains a distinct security boundary within this repository. A
long-lived fleet manager owns definitions and policy, isolated per-VM runtimes
own guest construction and execution handles, and a separate `vmm` client
receives only the control or runtime byte-channel authority needed for one command.

## Application development

The in-tree executables use the SDK's partial Rust std port. Application
command-line interfaces use clap; the formal shell retains quotes in its own
lexer/parser until expansion. Formatting, collections, paths and ordinary
command output use std. `echo` supports `-n`, `-e` and `-E`; unknown options such
as `--help` are text, and the first operand ends option parsing. Other public
commands provide clap help and reject extra or conflicting arguments. Shell
syntax and builtin errors return to the interactive prompt.

`hyper_rt::process::startup()` transfers Native capabilities once to a std
application. The runtime retains standard streams and the bootstrap Console
through std cleanup and TLS destruction. Use `std::io` for ordinary output;
borrowed runtime handles are available for Native readiness waits and routing.

Use `std::thread` and `std::sync` for application threads and synchronization;
these now use Native thread and atomic wait/wake syscalls. Ordinary sleeps use
`std::thread::sleep`. Absolute deadlines passed to Native object waits retain
the SDK clock types, since std does not expose that capability wait contract.

Ordinary path-based filesystem and subprocess operations can use std. Explicit
directory/process capability delegation, VM operations and service channel
multiplexing use hyper-os because std does not represent those authority APIs.
The bootstrap manifest keeps its restrictive schema parser (including duplicate
field and escape rejection); its bounded collections now use Vec storage.
`make app-check`, `make app-test`, and `make test-native` validate the migration.

## Current scope

- Rust std applications with ordinary `main`, built as Native dynamic PIE;
- a root component manager, finite startup coordinator and independent journal,
  with static instance trees, scoped capability routes and demand activation;
- a byte-only physical Console service, a separate formal terminal service,
  and a session manager that supervises terminal and shell lifetimes;
- a bounded interactive shell which launches commands with explicit process
  authorities and handle-backed standard I/O;
- a capability-scoped VM manager, isolated runtime, and multi-client `vmm`
  control and virtual-console tool;
- reproducible AArch64 and RISC-V compilation through the installed `hyper-cargo` driver;
- compilation exclusively against the assembled Native SDK; and
- end-to-end CI validation with the kernel from the same commit.

The Kernel starts only `/init` and supplies the minimum bootstrap authorities:
the root `Directory`, process-construction authority, resource and task ownership, and a
Console capability. `init` validates `/etc/hyper/services.json` before it
starts any child, then constructs services in dependency order through the
transactional `ProcessBuilder` ABI. Normal bytes currently follow this route:

```text
physical Console <-> console-raw <-> terminal-service <-> shell / foreground command
                         ^                ^
                         | logs           | supervision
                  journal <--- services  session-manager
```

Bootstrap init retains management authority until handoff. It runs from the initial RAM
archive, mounts the independent system volume at `/system`, then switches it to
`/` before starting formal `component-manager`. The manager validates the instance
tree and prepares `system-startup` to validate resource migration before accepting
one atomic capability transfer. Precommit failure rolls back to stage-1 recovery.
After commitment, the manager owns new component supervisors; system-startup adopts
the running I/O VM and mounted filesystems into independent providers from `svc/`.
The coordinator submits bootstrap-retire to stop predecessors and detach the archive.
The I/O VM does not reboot. An unsuccessful provider handoff retains the resources
still needed by that provider.

The original root is temporarily mounted at `/boot`; after successful retirement
that mountpoint is empty. Original archive backing is reclaimed when its last
borrowed file/page owner releases it. Formal executable and library mappings
belong to the independent system filesystem and do not pin the boot archive.
The formal root contains real `/bin`, `/svc`, `/etc` and `/lib64` directories;
`/tmp` is writable RAM and `/data` is persistent storage. See
[boot and system deployment](../docs/boot-layout.md) and the
[handoff contract](../docs/bootstrap-handoff.md).

`console-raw` owns the physical Console and exposes separate byte-stream and
log interfaces. It has no line editing or terminal policy. The formal
`terminal-service` owns canonical/raw input, echo, conversion, software flow
control and ordered output. `session-manager` launches and supervises it and the
shell, without relaying ordinary data. The shell gives a scoped terminal lease
to a command with unredirected terminal stdin; in a pipeline this is the first
stage. Process exit restores the preceding lease's settings. Bootstrap
recovery uses raw transport directly and never starts a terminal service.

Background services receive stdin at EOF and independent stdout/stderr writers
registered with the journal. They receive no ordinary physical Console authority.
The journal mirrors bounded log traffic to raw console after terminal takeover. Terminal flow control
does not pause logs. Overflow is counted per source; this is not persistent log
storage. Kernel logging is unchanged, and either kind of log can still disturb an
interactive display. VM-manager delegates its log stream to its VM runtimes.

Standard I/O uses request-sized terminal reads and per-process private stream
connections; pipes remain binary ByteChannels. The terminal control capability is
separate from the stream connector. See [Native terminals](../docs/terminal.md)
for timing, EOF, flow-control, restart and client contracts.

The manifest's optional `virtual-machines.config` path selects a file of named
VM definitions. Board packaging generates `/data/vms.json` and gates its use on
I/O runtime readiness; standalone tests use files under `bootstrap/stage1/init/tests/config/`.
Init transfers the configuration File to the VM manager. The manager validates
and opens guest images, then creates a fresh child resource domain, task group,
creation lease and isolated runtime for each start. There are up to eight
business definitions. The business fleet has its own resource domain, created
only when vm-manager starts; the resident I/O VM remains in core services.
Native storage can run without vm-manager, and the default noncritical manager
cannot take core services down on exit. Admission remains subject to available
resources.

Each `vmm` invocation receives private control and capability channels. The
manager supports multiple clients and reports exhausted connection capacity.
A runtime owns its read-only shared serial ring, WaitSet subscriptions and
bounded output retention. Only one client may attach to a VM's console, while
other clients can still issue lifecycle requests. Guest output does not bypass
the runtime to reach the physical Console.

## Build

From the repository root, run:

```sh
make app                       # AArch64
make app ARCH=riscv64           # RISC-V Native applications
make test-native ARCH=riscv64   # std, processes, threads, and shell acceptance
```

The generated SDK is placed under `target/sdk/aarch64`, and dynamic PIE
application images are written to `target/app/aarch64` by default. Applications
that have no Rust DSO dependencies may set `HYPER_LINK_MODE=static` to select
the SDK's equivalent `libhyper.a` link path. The system tools and VM services
that consume `lib/` use dynamic linking; the integration image still executes
`echo-static`, a standalone C fixture, as the SDK static-link contract test.
Rust static linking remains covered by `std-test-static`. Both static and
dynamic system executables use the explicitly delegated
`/lib64/ld-hyper-<arch>.so` loader. The static userspace loader under
`/boot` serves bootstrap programs only.

Clap-based tools select `libhyper_clap_shared.so` for Native builds; the same
upstream parser is used directly for host tests. Command-specific argument
structures stay in each tool. Component declarations and shared build templates
are described in [Native component builds](../mk/README.md).

`ARCH=riscv64` selects separate SDK and app output directories and runs the same
service graph, including the userspace VM fleet. The optional
`bootstrap/stage1/init/config/services-console-only.json` profile contains the raw Console service without a VM fleet. Formal init
starts the terminal and shell through session-manager; failed bootstrap starts
the independent fallback shell.
Acceptance-only manifests live under `bootstrap/stage1/init/tests/config/`.
The same init and shell binaries support service graphs with or without a VM
manager; `vmm --help` does not require a running manager.

## Repository layout

```text
app/
  Cargo.toml          App discovery, shared dependency versions, lints and profiles
  Cargo.lock          Locked dependencies for the application and Rust DSO set
  cat/ chmod/ cp/ echo/ editor/ free/ grep/ handle/ ldd/ less/ ln/ ls/
  memstat/ mkdir/ mount/ mv/ ps/ rm/ rmdir/ shell/ top/ touch/ umount/ vmm/
  componentctl/ journalctl/
svc/
  component-manager/ system-startup/ bootstrap-retire/ journal/
  console-raw/ session-manager/ terminal-service/
  io-runtime/ fs-backend/ fs-fat/ fs-erofs/ vm-manager/ vm-runtime/
bootstrap/stage1/
  init/               Initial service graph and supervision handoff
    config/           Service manifests for VM-enabled and console-only startup
    tests/            Unit tests and acceptance-only configurations
  console-raw/ fallback-sh/
  io-runtime/ fs-backend/ fs-fat/ fs-erofs/ mount/ umount/
  userspace-loader/   Static bootstrap executable loader
lib/                  Shared application libraries, in the app Cargo workspace
  clap/               Common command-line parser DSO
  tool-args/          Argument parsing and process selection for system tools
  vm-policy/          VM fleet configuration, resource policy and image validation
  vm-support/         Guest loading, virtual devices and I/O backend protocols
  terminal/           Formal terminal client and wire contract
  text-ui/            Shared fullscreen input/display helpers for interactive text tools
  dynamic-loader/     Formal executable loader and runtime linker
stdlib/rust-std/      Common Rust standard-library DSO
tests/app/            Standalone Native acceptance programs
  vm-smoke/ terminal-probe/ fs-failure/
```

Each executable is its own Cargo package with `Cargo.toml` and `src/main.rs`.
The workspace discovers app directories automatically, excluding `.cargo/` and
`target/`. A new app declares its build inputs in its own manifest and its
installed name/profile in `component.mk`. App-specific dependencies stay in that
app's manifest; shared dependencies and build policy stay in the workspace.
Source-only edits do not change `Cargo.lock`.

Command-specific clap types and reusable implementation modules live in the
app's own `src/`; unit-test bodies
live in its own `tests/` and are included by that package's library. Packages
set `autotests = false` so Cargo does not also treat these unit-test files as
standalone integration targets. No command tests are loaded by init.

Run all host tests with `make app-test`, or select a package with
`make app-test APP_TEST_PACKAGE=hyper-ls`. This source-level host-test entry
uses the workspace's SDK source patches and does not require SDK assembly. `make app-check` and `make app` cover
all workspace members. Component staged names are independent of their
bootstrap or system installation directory.

Reusable OS interaction belongs to `stdlib/rust/hyper-os`; application-local
service and command policy remains under `app`. Shared application implementations
live in [`lib/`](../lib/README.md), and their Native DSOs are installed in
`/lib64/<arch>-hyper-hyper/`, also accessible through `/lib`.
Applications and Rust DSOs must be built and deployed together with the same
SDK, compiler, profile and features. Host tests use the rlib variants.

## System services

These executables consume startup capabilities and service channels supplied by
their supervisor. Their installed paths identify service roles; they are not
standalone shell commands. The selected deployment profile determines which
services are present. See [init configuration](../bootstrap/stage1/init/config/README.md) for the
manifest and board configuration inputs.

Bootstrap paths below identify initial launch locations. Their independent
formal replacements live on the independent root; they do not retain bootstrap loaders,
worker binaries or configuration directories. There are no root-level service or
tool aliases. Each launcher receives its own `process.environment-directory` for
configuration and workers, separately from the live root namespace capability.

| Installed executable | Source | Responsibility |
| --- | --- | --- |
| `/init` | [init](../bootstrap/stage1/init) | Validate the service manifest, construct the service graph, delegate capabilities and supervise services. Start console services before waiting for storage; keep Native services available if storage or fleet configuration is unavailable. |
| `/svc/console-raw` in each environment | [bootstrap](../bootstrap/stage1/console-raw), [formal](../svc/console-raw/) | Own physical Console byte transport and independent bounded log ingress. Preserve raw bytes; mirror service logs during and after bootstrap. |
| `/svc/session-manager` | [session-manager](../svc/session-manager/) | Supervise the formal terminal and its shell/session group; revoke old transport bindings and restart a fresh session after failure. Does not relay ordinary I/O. |
| `/svc/terminal-service` | [terminal-service](../svc/terminal-service/) | Serve one terminal endpoint: canonical/raw input, editing and echo, conversions, software flow control, scoped foreground leases, size/events and ordered output. |
| `/svc/io-runtime` in each environment | [bootstrap](../bootstrap/stage1/io-runtime), [formal](../svc/io-runtime/) | Load the board-configured Linux I/O VM, manage its assigned physical devices and backend connections, discover GPT partitions, bind authorized block ranges and transfer Native block capabilities to the filesystem manager. Forward aggregate mount readiness. Broker guest block/network connections and retire their backend resources. Included in I/O deployments. |
| `/svc/vm-manager` | [vm-manager](../svc/vm-manager/) | Own named VM definitions and lifecycle policy, handle `vmm` requests, launch and supervise per-VM runtimes, and expose the I/O VM's read-only observation entry. |
| `/svc/fs-backend` in each environment | [bootstrap](../bootstrap/stage1/fs-backend), [formal](../svc/fs-backend/) | Apply fstab, identify the filesystem on each authorized GPT partition, load its independent worker, publish mounts and serve mount/umount requests. Retain lifecycle ownership outside the file I/O path. |
| `/svc/fs-fat` in each environment | [bootstrap](../bootstrap/stage1/fs-fat), [formal](../svc/fs-fat/) | Validate and serve FAT32 media. Uses the common filesystem service library for shared-buffer RPC and parent failure; the kernel retains VFS, file locks and caching. Started on demand by the manager in its own environment; the formal worker takes over existing FAT mounts before bootstrap retirement. |
| `/svc/fs-erofs` in each environment | [bootstrap](../bootstrap/stage1/fs-erofs), [formal](../svc/fs-erofs/) | Serve immutable core EROFS media, including directory and symlink metadata. Validate checksums and bounds, and reject unsupported format extensions. Started on demand by fs-backend; the formal worker takes over the mounted system volume. |
| `/svc/component-manager` | [component-manager](../svc/component-manager/) | Act as formal root, validate instance trees and capability routes, activate providers on demand and own component lifetimes. |
| `/svc/system-startup` | [system-startup](../svc/system-startup/) | Perform finite bootstrap adoption, formal mounts and initial VM provisioning. Submit prepared processes to the manager and arrange retirement, then exit. |
| `/svc/journal` | [journal](../svc/journal/) | Capture attributed service streams, retain bounded volatile records, mirror serial output and answer queries. |
| `/svc/bootstrap-retire` | [bootstrap-retire](../svc/bootstrap-retire/) | Stop and join transferred predecessors, drain console output and normally unmount the boot archive. |
| `/svc/vm-runtime` | [vm-runtime](../svc/vm-runtime/) | Construct and run one managed guest: load its image, configure vCPUs and virtual devices, connect I/O backends, serve its console and control requests, and retire resources on termination. Started by the VM manager for each VM start. |

The resident Linux I/O VM runs physical device drivers. `io-runtime` is the
Native service that manages that VM; `vm-runtime` manages an individual business
guest. [vm-smoke](../tests/app/vm-smoke/README.md) is a separate privileged acceptance fixture,
not an interactive command or production service.

## HypeR commands

The commands below expose Native capabilities, objects and VM services. Their
names may resemble Unix tools, but the objects and accounting they report are
HypeR-specific. Use `COMMAND --help` for the complete option list.

### `mount` and `umount`: Native volume administration

[mount](mount/) lists active managed volumes without arguments. `mount -a`
applies automatic `/etc/fstab` entries; `mount /data` or
`mount PARTLABEL=hyper-data` selects one entry there. An explicit `-T` path
selects another table; `/boot/etc/fstab` exists only while bootstrap is retained. `mount -t auto -o ro PARTLABEL=backup /mnt/backup` selects an authorized
GPT partition and target. Types are `auto`, `fat`/`vfat`, and `erofs`; EROFS requires
`ro`. The manager identifies media before selecting an installed worker, and
reports unavailable volumes, format mismatches and missing workers separately.

[umount](umount/) synchronizes and detaches one idle target. Open files,
directory/cwd handles, active lookups or nested mounts return Busy and preserve
the existing mount. It has no forced or lazy mode. A clean shutdown returns the
worker's exclusive block owner to the manager for a later mount; a crashed
worker is not silently reactivated.

The formal manager reports unexpected worker exits through its journal stream.
`componentctl status /fs-backend` shows the failure summary; `mount` includes
each worker's process KOID and terminal reason. A failed volume does not stop
the manager or its other workers.

Both commands need filesystem control authority, not raw device access. The
formal session manager passes it to its shell, which delegates only WAIT|WRITE
to `/bin/mount` and `/bin/umount`. Independent static recovery
copies remain in bootfs for failures before system takeover. See
[filesystem services](../docs/filesystem-services.md) for the protocol and
[boot layout](../docs/boot-layout.md) for supported fstab options.

### `vmm`: VM lifecycle and console access

[vmm](vmm/) is the shell client for the VM manager. It lists definitions and
observed VMs, reports state and memory backing, starts/stops/restarts managed
guests, changes vCPU affinity and attaches to a guest's serial console.

```text
vmm list
vmm status alpine
vmm start alpine --wait
vmm console alpine
vmm affinity alpine 0 1,3
vmm stop alpine --wait
vmm restart alpine --wait --timeout 60
vmm create test --config /data/new-vms.json
vmm delete test
```

No subcommand means `list`; VM operations require a name. `start`, `stop` and
`restart` normally report admission; `--wait` polls for `running` or `stopped`
and returns failure if the VM fails or the deadline expires. `--timeout` sets
the total wait in seconds (default 30). Timeout does not cancel an admitted
operation; inspect `vmm status` before retrying. `running` means vCPU execution
has started, not that guest userspace or networking is ready. Ctrl-] opens the
console detach menu. Only one client may attach to a VM's console at a time.
The affinity example allows vCPU 0 to run on host CPUs 1 or 3; CPU numbers are
zero-based. Automatic Fair balancing can move a Ready vCPU within this set.
Select one CPU to keep the vCPU on that host CPU.

`create` reads a definition from a configuration file; `delete` removes a stopped
definition. Both change only the running manager's inventory, without editing
deployment configuration or deleting guest images. The resident I/O VM is
observable through `list` and `status`; its read-only authority does not permit
lifecycle or console operations through `vmm`, regardless of its configured name.
For the I/O VM, guest-agent statistics show Linux CPU busy/I/O wait and memory
used, available, free and cache accounting separately from allocated VM backing.
Memory used means `MemTotal - MemAvailable`; CPU is normalized across all guest
vCPUs. Samples refresh every five seconds. An unavailable, unsupported or stale
agent is shown explicitly; the first CPU observation is marked `sampling`.
See [named VMs](../docs/applications.md#named-virtual-machines) and
[console and storage visibility](../docs/applications.md#console-and-storage-visibility).

### `handle`: kernel objects and capability inspection

[handle](handle/) answers which objects exist, which process holds a handle to
an object, and what that handle permits. With no arguments it lists visible
kernel objects. A process selector shows its handle table; `--all` scans handle
tables across visible processes. Selectors accept an exact process name or KOID.

```text
handle
handle shell
handle io-runtime --kind physical-device
handle --all --right write
handle shell --summary
handle --list-kinds
handle --list-rights
```

Use `handle --object KOID` to inspect one object, `handle PROCESS --handle HANDLE`
for one process-local handle, and `handle --all --object KOID` to find its visible
handle holders. Replace `KOID`, `PROCESS` and `HANDLE` with values from the lists.
IDs are printed in hexadecimal; input accepts decimal or `0x` hexadecimal.
A KOID identifies the underlying kernel object, while a handle number is local
to a process. Neither a printed ID nor inspector authority grants control of
the inspected object.

Single-object and single-handle queries include available type-specific details:
thread scheduler TID and lifecycle, VMAR ranges and mapping permissions, channel
peer and queue state, or physical device identity, IRQs and resource ranges.
For channels, the tool finds visible peer holders by scanning process handle
tables. `-v` adds purposes, raw rights/flags and reference counts by owner class;
`--no-headers` emits table rows suitable for pipelines. Scans may race with
object creation, exit or handle transfer and are not atomic system snapshots.
`--summary` groups filtered entries by kind: registry scans count objects;
handle scans report both handle counts and distinct object counts, so duplicated
handles do not look like additional objects. `--all --summary` aggregates across
visible processes without implying an atomic snapshot.
See the [inspection reference](../docs/applications.md#object-and-capability-inspection).

### `ldd`: shared-library dependency inspection

[ldd](ldd/) reads an executable or shared object's ELF metadata and reports its
interpreter and complete `DT_NEEDED` dependency graph. It does not execute the
input, load it into executable memory, or call constructors. The default
listing includes each dependency name once; paths resolve symbolic links, so
the `/lib` alias is displayed as `/lib64/<arch>-hyper-hyper/`. The
ELF's declared interpreter is shown separately from the loader file being checked.

```text
ldd /bin/vmm
ldd --tree /svc/vm-runtime
ldd --direct /bin/ps
ldd -v /bin/vmm /bin/handle
ldd --library-dir /data/candidate-libraries /bin/vmm
ldd --loader /data/candidate-loader --library-dir /data/candidate-libraries /bin/vmm
```

`--tree` shows which object requires each library, marking cycles and already
shown subtrees. `--direct` checks only the input's immediate dependencies and
loader. `-v` includes architecture, ELF OS ABI/version and SONAME. Static
executables are identified explicitly, and `--loader FILE` also checks their
selected loader. Missing libraries or loaders print `not found`;
unreadable, malformed or incompatible files have specific diagnostics. Multiple
operands are processed independently, with a failing exit status if any inspected
dependency cannot be resolved or validated.

The lookup directory defaults to `/lib/<arch>-hyper-hyper/` for the
input ELF architecture. Every `PT_INTERP` path is inspected literally; the canonical Native interpreter
is `/lib64/ld-hyper-<arch>.so`. `--library-dir`
inspects an alternate library set, and `--loader FILE` checks an explicitly
selected executable loader. These options are independent and do not change
the target program's runtime launch policy. The ELF declaration remains
visible even when the inspected loader has a different filesystem path.
No environment search path, RPATH or RUNPATH is applied. The command needs only
ordinary filesystem read authority, not inspector privileges. A process supplied
a different library Directory
capability can see different files. Inspection is not atomic across file changes.

Parsing bounds metadata reads and dependency traversal to the Native loader's
object/name limits. Debug sections and entire library contents are not read.
The tool does not verify symbols, relocations, future `dlopen` calls or process
capability grants; a successful listing is not proof that the image can run.
No runtime load addresses are invented for files that have not been executed.

### `ps`: process and thread inventory

[ps](ps/) lists visible Native processes with their KOIDs, immutable service
names, lifecycle states and active/pending thread counts. `ps -T` also groups
threads beneath their process and includes unowned kernel threads; per-CPU idle
and timer threads are labelled `idle/CPU` and `ktimer/CPU`. Native workers show
service-assigned names such as `journal-admit`, `terminal-recovery`, and
`console-input-admit`; the userspace thread cleanup worker is `thread-reaper`.
Applications assign these through `std::thread::Builder::name` or rename their
current thread with `hyper_os::thread::set_current_name`. Names are diagnostic
labels; use KOIDs to distinguish threads with the same role.
`ps --name vm-runtime` filters by a name substring.
`ps -p 0x100000001,0x100000002` selects multiple processes; IDs accept
decimal or hexadecimal and print as full-width hexadecimal, matching `handle`.
Name and ID filters intersect. `--no-headers` emits rows for pipelines. The IDs are
kernel-object identities, not Unix PIDs. Use `handle` for a thread's scheduler
TID and capability details, or `top` for CPU activity over time.

### `free`: physical memory accounting

[free](free/) derives total, used, free, reclaimable and estimated available
memory from the kernel's raw physical counters. `used = total - free -
reclaimable`, including reserved RAM. The ownership row separately partitions
managed allocations into non-reclaimable kernel memory, Native VMO backing,
guest backing and `buffered/cached`. `kernel` combines direct pages, heap and
page tables minus reclaimable slab. `buffered/cached` combines file-cache
backing and reclaimable slab, including file pages currently pinned or loading.
A guest's backend aliases do not count the same backing again. This is host
physical accounting, not the guest Linux view of free RAM.

`Cache` distinguishes reclaimable file and slab pages and explicitly reports
non-reclaimable payloads (pinned/loading pages). File reclaimability includes
shrinker-managed index/header backing, so warming the cache does not inflate
`used` by its disposable metadata. It is an estimate: live pins may retain a
minimum metadata set, and `available` is not an allocation guarantee.
Non-reclaimable payloads are included in both `buffered/cached` and `used`:
`used = reserved + kernel + user + guest + non-reclaimable cache + other`.
Use `free --bytes` to reconcile exact totals without display rounding.
`Ramfs` reports logical payload capacity within kernel memory; it is not
discardable cache or another
physical pool. Unknown censuses appear as `—` in dependent fields without hiding
valid physical totals. See [memory cache reporting](../docs/applications.md#memory-cache-reporting)
for the identities, overlap rules and estimation limits.

`free -b` prints exact bytes; `-k`, `-m` and `-g` select whole KiB, MiB and GiB.
`free -m -c 5 -s 0.5` collects five snapshots half a second apart, starting
immediately. Sampling is finite and defaults to one snapshot; `-s` alone does
not request continuous monitoring.

### `memstat`: allocation and cache diagnostics

[memstat](memstat/) reports allocator traffic, page ownership, buddy orders and
file-cache index/metadata occupancy. `memstat --save /tmp/before.json` saves a
baseline; `memstat --diff /tmp/before.json` shows changes after a workload in the
same boot. `--json` exports the full sample. `--classes` includes per-pool slab
sizes and large allocations when `CONFIG_HEAP_PROFILING=y` (default off).
Reads never flush or reclaim memory. These overlapping observations distinguish
allocation churn, live objects and retained cache slots; growth is not itself a
leak diagnosis. See [memory diagnostics](../docs/memory-diagnostics.md) for
accounting boundaries, configuration costs and sampling limitations.

### `top`: live CPU and process activity

[top](top/) samples CPU runtime counters, physical memory and per-process thread
runtime. Its CPU summary distinguishes Native user threads, kernel threads,
guest vCPUs and idle time. Use it to identify where CPU time is going while a
guest or Native command runs.

`top -d 0.5` refreshes every half second; `q` or Ctrl-C exits interactive mode.
`top -b -n 3` prints three snapshots without terminal control sequences or
keyboard input, suitable for capture in a file or pipeline. Process rows default
to descending CPU usage; `--sort name` or `--sort koid` changes the order.
`-p KOID[,KOID...]` and `--name TEXT` filter rows, and `--limit 10` bounds the
number displayed. CPU percentages use the total runtime across all host CPUs;
the summary remains system-wide even when process rows are filtered.

### `editor`: interactive text editing

`editor FILE` opens UTF-8 text for multiline editing. Ctrl-S saves, Ctrl-O chooses
a save path, Ctrl-F searches, and Ctrl-Q exits with confirmation for unsaved
changes. Arrows, Home/End and Page Up/Page Down move through the document.

It belongs to the formal system image and uses terminal-service through the
shared `hyper-text-ui` library. See [editor](../docs/editor.md) for bounds,
filesystem save behavior and verification commands.

### `less`: interactive paging

`less FILE` and `cat FILE | less` provide a read-only pager. Page Up/Page Down,
Space/b and arrows navigate; g/G jump to the beginning/end, `/` searches, `n`
finds the next match and `q` exits. Pipe data and terminal keystrokes use separate
connections, so a producer can finish before the user finishes paging. Redirected
stdout copies the input without display formatting. See [less](../docs/less.md)
for input bounds and verification commands.

### `sh`: the foreground Native shell

[shell](shell/) is installed as `/bin/sh` and is the normal interactive
shell. A separate static [fallback-sh](../bootstrap/stage1/fallback-sh) lives in
`/boot/svc/fallback-sh`. Stage-1 init launches it directly on failure before
ownership transfer. Formal init instead starts `session-manager`, which launches
the terminal service and `sh`.
Post-handoff failures remain under formal init; a missing system shell is retried
without starting bootstrap recovery.
The fallback service omits clap, history, pipelines and redirection, retaining
basic line editing, quoted arguments, `cd`, `pwd`, `help`, `exit` and external
command launch. It is not on the command search path. See the
[shell and recovery behavior](../docs/shell.md).

The formal shell provides cursor editing, completion, reverse history search,
multiline input and bracketed paste. Its language includes variables, command
and arithmetic substitution, globbing, control structures, functions, scripts,
pipelines, exit statuses, and ordered redirection with descriptor aliases and
here-documents. `echo` and `printf` are builtins; `/bin/echo` remains an
independent application. `status` shows the previous result and elapsed time.

History retains at most 256 entries in `/tmp/hyper-sh.history`, is bounded to
256 KiB on disk, and disappears with the default RAM-backed `/tmp` on reboot.
It never falls back to `/data` or a home directory. Leading whitespace opts out
of history; duplicates are omitted. Shell exit causes the session manager to
retire the session and start a fresh terminal and shell, subject to its bounded
restart policy. Background jobs and full Bash compatibility are outside this
implementation; see [shell syntax and limits](../docs/shell.md).

The session delegates process-construction authority to the shell. Commands
inherit attenuated root and cwd Directory capabilities, standard streams and
authorized process resources; path traversal is bounded by the delegated root,
not the initial cwd. Inspector delegation depends on the resolved executable
path: `/bin/ps` receives task inspection, `/bin/free` and `/bin/memstat` memory
inspection, and `/bin/top` task, memory and CPU inspection.
`/bin/perf` alone receives CPU profiling control and call-chain inspection.
`/bin/handle` receives object
inspection including privileged details, plus task inspection for process
lookup. Copying or
renaming a tool to another path does not acquire those grants.
The trusted `/bin/sh` path receives the shell's delegatable diagnostic
and control capabilities, so nested shells keep the same tool access. Every
formal child uses the system dynamic loader, including static executables; the
shell retains no bootstrap loader or archive directory.

## PMU profiling

`perf start`, `stop`, `status`, `dump` and `record` control bounded system-wide
PMUv3 cycle sampling on an opt-in AArch64 kernel. `record --output FILE -- COMMAND`
streams a capture while an ordinary command runs; `start` followed by `dump`
profiles shell-launched privileged tools. Captures include loss counters and the
kernel build identity. The host converter produces folded stacks and an SVG;
see [PMU sampling and flame graphs](../docs/profiling.md) for commands, permissions
and measurement limits. This does not replace `top`'s scheduler CPU-time stats.

## File tools

The normal profile installs these independent std/clap applications under
`/bin`. The default shell PATH searches only that directory, on the independent system root:

| Command | Supported operations |
| --- | --- |
| `ls [-ad1rtS] [--bytes] PATH...` | List entries or directory operands (`-d`), sort by name/size/modification time, and show permissions, sizes and symlink targets (`name@ -> target`, including dangling links). `-1` omits metadata and targets but retains known type suffixes. Unreadable child attributes appear as `?`; healthy entries remain visible, the failed path is reported on stderr, and the command returns failure. |
| `cat [-nbs] FILE...` | Stream files or stdin; number all/nonblank lines and squeeze repeated blank lines. State continues across inputs. |
| `grep [-ix] [-m NUM] PATTERN FILE...` | Filter text; `-m NUM` limits selected lines per input, `-x` requires a whole-line match, and `--line-buffered` flushes selected lines. See [text filtering](../docs/shell.md#grep). |
| `echo [-n] [-e\|-E] TEXT...` | Print arguments, optionally omit the newline or interpret backslash escapes. `--` ends option parsing. |
| `cp [-RnvT] SOURCE... DEST` | Copy files or trees; `-n` skips existing entries using exclusive file creation, `-T` selects an exact destination, and `-v` reports copied files/links. |
| `mv [-vT] SOURCE... DEST` | Rename files, links, or directories; `-T` selects an exact destination and `-v` reports moves. Multiple sources require a directory. |
| `ln [-snvT] TARGET LINK` | Create hard or symbolic links; `-n` does not enter a symlinked destination directory, `-T` treats LINK as an exact name, and `-v` reports the link. |
| `rm [-rdfv] PATH...` | Remove files or trees; `-d` removes only empty directories, `-f` ignores missing paths, and `-v` reports removed operands. |
| `chmod [-Rv] MODE PATH...` | Octal modes or symbolic clauses such as `u+rw,go-rwx`; `-v` shows each processed path and its old/new mode. |
| `mkdir [-pv] [-m OCTAL] PATH...` | Create directories and optional parents; `-v` reports newly created operand directories. Explicit modes apply to new final directories. |
| `rmdir [-pv] PATH...` | Remove empty directories; `-p` then removes empty ancestors, stopping at the first error, and rejects `..` components. `-v` reports each removal. |
| `touch [-acm] [-r REFERENCE] [-d @SECONDS] PATH...` | Create files or update timestamps without truncation. `-d` accepts nonnegative Unix seconds with up to nine fractional digits; it conflicts with `-r`. |

Tools accept `--help` (except `echo`, where it is text) and `--` before operands
beginning with `-`. File operations report path-specific errors and return
failure if any requested operation fails. `grep` returns 1 for no matches and
2 for errors; its `-q` mode can return success after finding a match despite an
earlier input error.
`chmod` treats an omitted user/group/other selector as `a`; Native mode bits do
not imply a Unix credential or umask implementation. Recursive chmod skips
nested symbolic links; an explicitly named symbolic link changes its target.

Recursive `cp` preserves symbolic links, including dangling links. It refuses
existing destination symlinks, same-file copies (including hard links), and
copying a directory into itself. Regular destination files are overwritten unless `-n` is selected.
`ln` refuses existing link names. `mv` uses filesystem rename and reports
cross-filesystem moves as unsupported by that operation; it does not silently
fall back to a non-atomic copy/delete sequence. Recursive `rm` does not follow
symbolic links and refuses the root and final `.`/`..` operands. These are basic
file tools, not full GNU coreutils option compatibility.

## Standard library and Native services

Use std for ordinary files, standard output, argument handling, allocation,
threads, synchronization and elapsed-time policy. File tools already follow
this boundary. The VM manager reads its supplied configuration through std,
and the VM runtime reads its supplied image through `std::fs::File` and
`std::os::hyper::fs::FileExt`. Both consume the original capability through
`hyper_os::fs::File::into_std()`; they do not reopen it by an ambient path.
The runtime retains the READ-authorized Native image-length query because std
metadata requires INSPECT, which its image capability deliberately lacks.

Native SDK calls remain where their semantics are required:

- VM construction, guest VMO writes, virtual serial and VM lifecycle;
- capability transfer, resource domains, scoped process construction and
  inspection (`ps`, `handle`, `free`, `memstat`, `top`);
- terminal leases, request-sized streams and multi-object waits, including the
  shell's foreground input handoff and `vmm console` multiplexing;
- absolute Native deadlines supplied to those waits and service protocols.

The shell emits its own prompts and diagnostics through std stdout and flushes
before blocking. Input ownership and child-channel routing remain Native.
Terminal stdin uses request-sized reads so handing foreground ownership to a
child does not strand unread bytes in a shell-local buffer. Init also uses std,
retaining Native APIs for bootstrap and delegation.

## Planned work

The shared [roadmap](../docs/roadmap.md) owns project commitments. Existing
Native application functionality remains supported while I/O backend and
hardware qualification work continues. Multi-UART transport discovery and
wiring are not yet implemented; the current deployment has one physical console.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).

## Service administration

`componentctl` lists component state and supports status, route inspection,
start, stop and restart. `journalctl` reads bounded volatile logs, with
`-c COMPONENT_PATH`, `-n COUNT` and `-f`. For automated inspection,
`journalctl --json [-c COMPONENT_PATH] [--after N] [--through N] [--limit N]`
returns one JSON query page, including sequence cursors, source generations and
retention/rejection/mirror-loss counters. The limit is 1–128 records (also bounded
by the journal's page byte budget); continue from `next`, fixing `through` to the
first page's `newest` for a stable snapshot. JSON paging is separate from the
human-oriented `--lines`/`--follow` mode. The trusted shell delegates separate
control and query capabilities to these commands. See
[component management](../docs/services.md) for routes, activation and ownership limits.
