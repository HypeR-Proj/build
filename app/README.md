<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# HypeR Native applications

This directory contains ordinary Native tools and system services.
The RAM-resident boot and recovery programs live in
[`bootstrap/stage1`](../bootstrap/stage1/README.md). Both source domains share
the Cargo workspace and lockfile here, including reusable libraries in `lib/`.
Target builds consume the assembled
Native SDK; source-level host tests use workspace SDK patches. Applications
do not reach into kernel or runtime implementation internals.

VM management remains a distinct security boundary within this repository. A
long-lived fleet manager owns definitions and policy, isolated per-VM runtimes
own guest construction and execution handles, and a separate `vmm` client
receives only the control or runtime byte-channel authority needed for one command.

## Application development

The in-tree executables use the SDK's partial Rust std port. Command-line interfaces
use clap, shell words use shlex, and formatting, collections, paths, and ordinary
command output use std. `echo` supports `-n`, `-e` and `-E`; unknown options such
as `--help` are text, and the first operand ends option parsing. Other public
commands provide clap help and reject extra or
conflicting arguments. Shell builtins use `try_parse_from` so help and argument
errors return to the prompt rather than terminating the shell. Shell quoting
follows POSIX shlex rules, including comments and double-quote escaping.

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
- a bounded, declarative service manifest loaded by `init` through a root
  `Directory` capability;
- direction-attenuated physical Console workers and an isolated foreground
  session manager;
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
physical Console <-> Console input/output workers <-> raw ByteChannels
                 <-> virtual console manager <-> shell <-> command
```

The active init retains management authority. The default deployment keeps
bootstrap programs in RAM at `/boot` and system programs on EROFS at `/system`.
A separately launched `init-system` adopts the live core service
supervisors through one atomic capability transfer before boot init exits.
Pre-commit failure leaves boot init supervising recovery services. See
[boot and system deployment](../docs/boot-layout.md). Choosing
`BOARD_SYSTEM_LAYOUT=ramfs` stores `/system` in RAM with the same handoff and
root switch. After adopting supervision, system init prepares a new ramfs root
and atomically publishes it with the original root mounted at `/boot` and
existing disk mounts at their configured paths. RAM-resident `/system` is promoted and detached from its old directory in the
same transaction, without copying files. The formal root has no path aliases. Root-switch failure leaves the old namespace usable
and core services supervised by system init, without starting the system graph.

Each Console worker receives only one
physical direction plus one matching byte-channel direction; the session
manager receives no physical Console authority. Capability attenuation is
monotonic; the Console workers and virtual console manager cannot delegate their
physical transport endpoints. The manager creates fresh client channels before
starting a noncritical shell and supervises its lifetime. Shell exit or failure
restarts only that client, with a delay for rapid failures.
The two blocking workers provide a genuinely duplex data plane without
polling or a generic per-byte protocol header. Manifest purposes are symbolic,
image-scoped service-contract names; init resolves them to typed startup
purposes before creating any process. Application code depends on the safe
`hyper-os` binding and does not call the raw syscall crate or C runtime
directly.

Session and VM services also receive `stdio.output` and `stdio.error` write
endpoints, so ordinary `println!` and `eprintln!` work. These feed the Console
output worker directly, bypassing the session relay to avoid a dependency on
the logging service consuming its own queue. VM-manager duplicates these
streams into each VM-runtime; the Rust runtime owns them through process exit.
The output queue is bounded and applies backpressure when full. It carries no
read authority, and process termination closes that process's copies without
closing other writers. The shell retains its foreground streams. Console
workers keep only their existing transport endpoints and receive no standard
streams; other background services receive no stdin.

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
`/system/lib64/ld-hyper-<arch>.so` loader. The static userspace loader under
`/boot` serves bootstrap programs only.

Clap-based tools select `libhyper_clap_shared.so` for Native builds; the same
upstream parser is used directly for host tests. Command-specific argument
structures stay in each tool. Component declarations and shared build templates
are described in [Native component builds](../mk/README.md).

`ARCH=riscv64` selects separate SDK and app output directories and runs the same
service graph, including the userspace VM fleet. The optional
`bootstrap/stage1/init/config/services-console-only.json` profile contains console workers and the
virtual console manager without a VM fleet. The manager starts its shell.
Acceptance-only manifests live under `bootstrap/stage1/init/tests/config/`.
The same init and shell binaries support service graphs with or without a VM
manager; `vmm --help` does not require a running manager.

## Repository layout

```text
app/
  Cargo.toml          App discovery, shared dependency versions, lints and profiles
  Cargo.lock          Locked dependencies for the application and Rust DSO set
  cat/ chmod/ cp/ echo/ free/ grep/ handle/ ldd/ ln/ ls/ mkdir/ mv/ ps/ rm/ rmdir/ top/ touch/
  init/ vm-manager/ vm-runtime/ vm-smoke/ vmm/
bootstrap/stage1/
  init/               Initial service graph and supervision handoff
    config/           Service manifests for VM-enabled and console-only startup
    tests/            Unit tests and acceptance-only configurations
  console-input/ console-output/ session/ fallback-sh/
  io-runtime/ fs-backend/ fs-fat/ fs-erofs/ mount/ umount/
  userspace-loader/   Static bootstrap executable loader
lib/                  Shared application libraries, in the app Cargo workspace
  clap/               Common command-line parser DSO
  tool-args/          Argument parsing and process selection for system tools
  vm-policy/          VM fleet configuration, resource policy and image validation
  vm-support/         Guest loading, virtual devices and I/O backend protocols
  service-manager/    Shared stage-1 and system-init implementation
  dynamic-loader/     Formal executable loader and runtime linker
  rust-std/           Common Rust standard-library DSO
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

Reusable OS interaction belongs to `lib/rust/hyper-os`; application-local
service and command policy remains under `app`. Shared application implementations
live in [`lib/`](../lib/README.md), and their Native DSOs are installed in
`/system/lib64/<arch>-hyper-hyper/`, also accessible through `/system/lib`.
Applications and Rust DSOs must be built and deployed together with the same
SDK, compiler, profile and features. Host tests use the rlib variants.

## System services

These executables consume startup capabilities and service channels supplied by
their supervisor. Their installed paths identify service roles; they are not
standalone shell commands. The selected deployment profile determines which
services are present. See [init configuration](../bootstrap/stage1/init/config/README.md) for the
manifest and board configuration inputs.

The bootstrap paths below describe their initial launch locations. After the
root switch they reside under `/boot`, and formal services remain under `/system`.
There are no root-level service or tool aliases. Core launchers retain a fixed
`process.environment-directory` for their own files; their separate root
capability follows the live namespace.

| Installed executable | Source | Responsibility |
| --- | --- | --- |
| `/init` | [init](../bootstrap/stage1/init) | Validate the service manifest, construct the service graph, delegate capabilities and supervise services. Start console services before waiting for storage; keep Native services available if storage or fleet configuration is unavailable. |
| `/svc/console-input` | [console-input](../bootstrap/stage1/console-input) | Read the physical console, normalize input newlines and forward bytes to the session through a channel. Holds only the input direction of the physical console. |
| `/svc/console-output` | [console-output](../bootstrap/stage1/console-output) | Drain the shared output channel to the physical console. Holds only the output direction and applies backpressure through the bounded channel. |
| `/svc/session` | [session](../bootstrap/stage1/session) | Own the virtual console, relay foreground input/output, launch the shell and restart it after exit or failure. The console transport survives each shell instance. |
| `/svc/io-runtime` | [io-runtime](../bootstrap/stage1/io-runtime) | Load the board-configured Linux I/O VM, manage its assigned physical devices and backend connections, discover GPT partitions, bind authorized block ranges and transfer Native block capabilities to the filesystem manager. Forward aggregate mount readiness. Broker guest block/network connections and retire their backend resources. Included in I/O deployments. |
| `/system/svc/vm-manager` | [vm-manager](vm-manager/) | Own named VM definitions and lifecycle policy, handle `vmm` requests, launch and supervise per-VM runtimes, and expose the I/O VM's read-only observation entry. |
| `/svc/fs-backend` | [fs-backend](../bootstrap/stage1/fs-backend) | Apply fstab, identify the filesystem on each authorized GPT partition, load its independent worker, publish mounts and serve mount/umount requests. Retain lifecycle ownership outside the file I/O path. |
| `/svc/fs-fat` | [fs-fat](../bootstrap/stage1/fs-fat) | Validate and serve FAT32 media. Uses the common filesystem service library for shared-buffer RPC and parent failure; the kernel retains VFS, file locks and caching. Started on demand by fs-backend. |
| `/svc/fs-erofs` | [fs-erofs](../bootstrap/stage1/fs-erofs) | Serve immutable core EROFS media, including directory and symlink metadata. Validate checksums and bounds, and reject unsupported format extensions. Started on demand by fs-backend. |
| `/system/svc/init-system` | [init](init/) | Validate the combined service graph, atomically adopt existing core supervisors, switch the root namespace, then start system services and continue whole-system supervision. Retain core supervision if root switching fails. Used with both disk-backed and RAM-resident system files. |
| `/system/svc/vm-runtime` | [vm-runtime](vm-runtime/) | Construct and run one managed guest: load its image, configure vCPUs and virtual devices, connect I/O backends, serve its console and control requests, and retire resources on termination. Started by the VM manager for each VM start. |

The resident Linux I/O VM runs physical device drivers. `io-runtime` is the
Native service that manages that VM; `vm-runtime` manages an individual business
guest. [vm-smoke](vm-smoke/README.md) is a separate privileged acceptance fixture,
not an interactive command or production service.

## HypeR commands

The commands below expose Native capabilities, objects and VM services. Their
names may resemble Unix tools, but the objects and accounting they report are
HypeR-specific. Use `COMMAND --help` for the complete option list.

### `mount` and `umount`: Native volume administration

[mount](../bootstrap/stage1/mount) lists active managed volumes without arguments. `mount -a`
applies automatic boot-environment `etc/fstab` entries; `mount /data` or `mount PARTLABEL=hyper-data` selects
one fstab entry. `mount -t auto -o ro PARTLABEL=hyper-system /system` selects an authorized
GPT partition and target. Types are `auto`, `fat`/`vfat`, and `erofs`; EROFS requires
`ro`. The manager identifies media before selecting an installed worker, and
reports unavailable volumes, format mismatches and missing workers separately.

[umount](../bootstrap/stage1/umount) synchronizes and detaches one idle target. Open files,
directory/cwd handles, active lookups or nested mounts return Busy and preserve
the existing mount. It has no forced or lazy mode. A clean shutdown returns the
worker's exclusive block owner to the manager for a later mount; a crashed
worker is not silently reactivated.

Both commands need filesystem control authority, not raw device access. The
session passes it to the shell, which delegates only WAIT|WRITE to the exact
mount/umount paths in `/bin`, `/boot/bin` and `/system/bin`. They are statically
available in bootfs even when `/system` cannot mount. See
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
zero-based, and the allowed set does not enable automatic load balancing.

`create` reads a definition from a configuration file; `delete` removes a stopped
definition. Both change only the running manager's inventory, without editing
deployment configuration or deleting guest images. The resident I/O VM is
observable through `list` and `status`; its read-only authority does not permit
lifecycle or console operations through `vmm`, regardless of its configured name.
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
the `/system/lib` alias is displayed as `/system/lib64/<arch>-hyper-hyper/`. The
ELF's declared interpreter is shown separately from the loader file being checked.

```text
ldd /system/bin/vmm
ldd --tree /system/svc/vm-runtime
ldd --direct /system/bin/ps
ldd -v /system/bin/vmm /system/bin/handle
ldd --library-dir /data/candidate-libraries /system/bin/vmm
ldd --loader /data/candidate-loader --library-dir /data/candidate-libraries /system/bin/vmm
```

`--tree` shows which object requires each library, marking cycles and already
shown subtrees. `--direct` checks only the input's immediate dependencies and
loader. `-v` includes architecture, ELF OS ABI/version and SONAME. Static
executables are identified explicitly, and `--loader FILE` also checks their
selected loader. Missing libraries or loaders print `not found`;
unreadable, malformed or incompatible files have specific diagnostics. Multiple
operands are processed independently, with a failing exit status if any inspected
dependency cannot be resolved or validated.

The lookup directory defaults to `/system/lib/<arch>-hyper-hyper/` for the
input ELF architecture. The canonical `PT_INTERP` name
`/lib64/ld-hyper-<arch>.so` selects `/system/lib64/ld-hyper-<arch>.so` for
inspection; other interpreter names are inspected literally. `--library-dir`
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
threads are labelled `idle/CPU`. `ps --name vm-runtime` filters by a name
substring. `ps -p 0x100000001,0x100000002` selects multiple processes; IDs accept
decimal or hexadecimal and print as full-width hexadecimal, matching `handle`.
Name and ID filters intersect. `--no-headers` emits rows for pipelines. The IDs are
kernel-object identities, not Unix PIDs. Use `handle` for a thread's scheduler
TID and capability details, or `top` for CPU activity over time.

### `free`: physical memory accounting

[free](free/) reports HypeR's total, used, free and reserved physical memory,
reclaimable cache and buffer usage, and ownership by kernel, heap, page tables,
Native users and guests. `free -b` prints bytes instead of human-readable sizes;
`-k`, `-m` and `-g` select whole KiB, MiB and GiB. `free -m -c 5 -s 0.5` collects
five snapshots half a second apart, starting immediately. Sampling is finite
and defaults to one snapshot; `-s` alone does not request continuous monitoring.
This is host physical accounting, not the guest Linux view of free memory.

`cache` includes reclaimable VFS pages and allocator CPU caches and is already
part of `used`; `buffers` also overlaps those totals. Do not add these columns
as separate pools. An unavailable cache sample is shown as `—`, not zero.
See [memory cache reporting](../docs/applications.md#memory-cache-reporting) for
the accounting definitions.

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

### `sh`: the foreground Native shell

[shell](shell/) is installed as `/system/bin/sh` and is the normal interactive
shell. A separate static [fallback-sh](../bootstrap/stage1/fallback-sh) lives in
`/boot/svc/fallback-sh`. Session waits for startup completion before launching
`sh`; startup failure or an unavailable system shell selects `fallback-sh`.
The fallback service omits clap, history, pipelines and redirection, retaining
basic line editing, quoted arguments, `cd`, `pwd`, `help`, `exit` and external
command launch. It is not on the command search path. See the
[shell and recovery behavior](../docs/shell.md).

The formal shell provides bounded line editing,
quoting, the `cd`, `pwd`, `help`, `clear` and `exit` builtins, external command
launch, concurrent pipelines and file redirection. `echo` is an external
application. Up/Down recalls up to 32 commands and restores the unfinished
input when moving past the newest entry. Ctrl-U clears the line; Ctrl-L redraws
the screen. History is local to the shell instance; commands beginning with
whitespace and consecutive duplicates are omitted. Exiting the shell causes the session service to start a new shell.
See [shell syntax and limits](../docs/shell.md) for supported syntax.

The session delegates process-construction authority to the shell. Commands
inherit attenuated root and cwd Directory capabilities, standard streams and
authorized process resources; path traversal is bounded by the delegated root,
not the initial cwd. Inspector delegation depends on the resolved executable
path: `/system/bin/ps` receives task inspection, `/system/bin/free` memory
inspection, and `/system/bin/top` task, memory and CPU inspection.
`/system/bin/handle` receives object
inspection including privileged details, plus task inspection for process
lookup. Copying or
renaming a tool to another path does not acquire those grants.
The trusted `/system/bin/sh` path receives the shell's delegatable diagnostic
and control capabilities, so nested shells and recovery-to-formal launches keep
the same tool access. It also retains the bootstrap loader for recovery tools.

## File tools

The normal profile installs these independent std/clap applications under
`/system/bin`, leaving static recovery tools in `/boot/bin`. The placement and
shell search order are the same for disk-backed and RAM-resident `/system`:

| Command | Supported operations |
| --- | --- |
| `ls [-ad1rtS] [--bytes] PATH...` | List entries or directory operands (`-d`), sort by name/size/modification time, and show permissions, sizes and symlink targets (`name@ -> target`, including dangling links). `-1` omits metadata and targets but retains type suffixes. |
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
  inspection (`ps`, `handle`, `free`, `top`);
- console/session channel relays and multi-object waits, including the shell's
  foreground input handoff and `vmm console` multiplexing;
- absolute Native deadlines supplied to those waits and service protocols.

The shell emits its own prompts and diagnostics through std stdout and flushes
before blocking. Input ownership and child-channel routing remain Native;
buffering that input in std while handing its channel to another consumer
would require a separate handoff protocol. Init also uses std, retaining Native APIs for bootstrap and delegation.

## Planned work

The shared [roadmap](../docs/roadmap.md) owns project commitments. Existing
Native application functionality remains supported while I/O backend and
hardware qualification work continues. Multi-UART transport discovery and
wiring are not yet implemented; the current deployment has one physical console.

## License

Licensed under the Apache License, Version 2.0. See
[the project license](../LICENSE).
