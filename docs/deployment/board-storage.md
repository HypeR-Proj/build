<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Board deployment and storage

`boards/qemu.json` and `boards/rpi5.json` describe deployment policy for the
same AArch64 kernel and applications. Hardware addresses and interrupt topology
come from the host and guest device trees. Selecting a board does not produce
a different kernel or app ABI.

## Disk ownership

The image builder creates exactly the outer GPT partitions declared in
`disk.partitions`. The default deployment uses `hyper-data` (1 GiB writable
FAT), `hyper-system` (64 MiB read-only EROFS), and an opaque Alpine disk
(2 GiB). Partition labels and UUIDs are deployment policy, not backend names.
The data payload includes `board.json`, `vms.json`, `partitions.json`, and guest
ITBs. The I/O VM ITB lives only in the bootstrap archive at `io-vm.image`.
QEMU loads this archive from the host; Pi firmware needs it on the FAT data
partition too, where it remains visible as `/data/bootstrap.cpio`.
Guest partitions contain whole virtual disks, including any guest partition table.

A QEMU deployment without persistent data or configured guests may omit the data
partition. If it retains VM manager, its empty initial fleet is packaged at
`/etc/hyper/vms.json` in the formal system filesystem. Deployments with a data
partition continue to load the editable fleet from `/data/vms.json`.

Linux drives the physical controller and enumerates whole physical block devices.
Native io-runtime reads their GPT headers and entry arrays through bounded block
reads, verifies CRCs, UUIDs and extents, and resolves `PARTUUID=` or `PARTLABEL=`
selectors against the actual media. Duplicate matching labels are ambiguous;
use PARTUUID to distinguish them. One valid GPT copy permits recovery; two valid
but conflicting copies are rejected. Discovery waits for asynchronous device
probe within a bounded startup deadline.

An image flashed onto a larger SD card keeps the GPT backup at the original
image boundary. Discovery follows that location only from a CRC-checked primary
header with valid metadata ranges. A stale GPT at the larger card's physical end
is outside that declared pair and is ignored. Discovery does not resize
partitions or rewrite GPT metadata. If the
primary header is corrupt, only a valid backup at the physical media end can
recover discovery: it does not scan the disk or trust a damaged header's pointer.

Only the owner control mailbox (client zero) can enumerate, read raw metadata or
bind ranges. It has a private 1 MiB scratch grant and is not a filesystem client.
For each authorized Native or guest client, HypeR sends a disk identity, LBA range
and read-only flag. Linux checks bounds and writable overlap and installs an
immutable dm-linear/LIO export. It does not parse GPT, select partitions by name,
or mount their filesystems. Guest runtimes cannot forward owner commands through
the broker. Native filesystem workers receive only their selected block capability.

HypeR's Native block initiator uses standard virtio-scsi split queues and shared
pages. `io-runtime` creates an independent initiator for each declared Native
volume and moves its activated capability to `fs-backend`. The manager applies the
bootstrap fstab first; formal init requests `/etc/fstab` after handoff and
root switching. The manager detects media and starts a separate worker per mounted
volume. The manager publishes the worker transport beneath the RAM namespace:
`/data` is writable FAT; the default deployment mounts its read-only EROFS volume at
`/system` before stage-1 switches it to `/`. File reads access the volume through the generic kernel VFS and cache;
they do not populate
a second ramfs copy. Cache misses and mutations use bounded shared-memory RPC
directly to the worker. See [filesystem services](https://hyper-proj.github.io/lib-fs-service/docs/filesystem-services.html) and
[FAT semantics and durability](https://hyper-proj.github.io/fs-fat/docs/fat.html).

## Bootstrap and VM services

The bootstrap initramfs contains enough to start init, the Native services,
and the Linux I/O VM before `/data` exists. The image builder projects the same
board JSON into bounded Native and Linux client manifests. Linux kernel and
module binaries remain digest-verified appliance artifacts; HypeR contributes
a separate configuration archive to the Linux initramfs.

Core console services start independently. Stage-1 failures before supervision
transfer select `svc/fallback-sh` in the boot environment. After handoff, formal
init handles failures and starts `/bin/sh`. The bootstrap readiness
channel precedes handoff; formal init mounts `/data` before loading its VM fleet
configuration.
The manager acknowledges the complete fleet configuration before attempting
autostart, including an empty fleet or one with no autostart VMs. Init supervises
configuration admission and the manager service; guest lifecycle failures remain
local to the manager. The I/O VM continues running under io-runtime independently.
Guest lifecycle management remains in vm-manager and each guest's vm-runtime.
If the optional I/O runtime cannot start, exits, closes or corrupts its readiness
reply, or misses the 120-second deadline, init retains the Native console, shell
and service supervision. A failed required mount does not deliberately terminate
a still-live I/O provider or filesystem manager; recovery mount commands remain
available. An unreadable or rejected fleet configuration likewise leaves these
services available. If `/system` is usable, system init can adopt the core
services, switch root and start the VM manager even when `/data` failed.
That manager continues to answer inspection requests and refuses VM creation
and startup until configuration is available on a subsequent boot. If the
handoff fails, boot init retains core supervision and its recovery shell. If the
root switch fails after handoff, formal init retains core supervision and its
formal console; the system service graph is not launched. The VM manager does not
silently retry provisioning or assume that an unobservable I/O VM name is free.
Formal init retains its console for startup failures when possible; critical
failures during steady supervision remain fatal. Neither reopens bootstrap recovery. Provider failure does not
waive the kernel's DMA retirement requirements.

The manager retains each I/O session endpoint until the runtime reports its VM
installed, then transfers it to the I/O broker through the supervisor wait set.
Image loading does not consume the bounded I/O-handshake deadline or occupy
the shared broker listener. Pending endpoints retire with their owning instance.
Disk and network setup share a capability channel to io-runtime. Only negotiation,
reset and release commands pass through that service; queue notifications and
shared-page data transfers do not.

Ordinary guest memory need not be physically contiguous. A dynamic backend
mapping retains the exact guest-memory grant and exposes a kernel-authorized
extent table to the Linux module. Admission binds one generation token to one
client's primary notification route: storage when present, otherwise network.
After all configured vhost endpoints drain and Linux drops its mappings and
page references, the module reports quiescence on that route. HypeR then clears
the aliases and waits for every CPU's translation invalidation acknowledgment
before releasing the old storage. A timeout or process exit is not proof that
DMA has stopped.

## Build and run

Interactive `make run` / `make board-run` uses QEMU's multiplexed serial console.
Press `Ctrl+A`, then `X` to exit QEMU, or `Ctrl+A`, then `C` to switch to the
QEMU monitor. `Ctrl+C` is passed to the guest.
Piped input, including automated tests, uses a dedicated serial backend without
monitor multiplexing.

The image builder needs `mkfs.fat` from dosfstools and `mcopy` from mtools,
in addition to the normal build prerequisites. Generated EROFS volumes also
need `mkfs.erofs`; `EROFS_MKFS` supplies an explicit tool path. It searches `PATH` first,
then queries `brew --prefix` for each package's `bin` and `sbin` directories
when Homebrew is available. Explicit `--mkfs` and `--mcopy` executable paths
override automatic discovery.

```sh
make board-plan BOARD=qemu
make board-image BOARD=qemu
make board-run BOARD=qemu
```

Plain AArch64 `make` creates the complete board image only when missing;
otherwise it updates the host-side kernel/bootstrap and the generated EROFS
system volume, preserving data and guest partitions.
`make rebuild` atomically repacks the full disk, resetting its data.
`make run` only starts existing artifacts.
The Alpine volume is initialized from the pinned base rootfs as an ext4 disk.
Alpine boots it through I/O VM virtio-scsi; `apk` and BusyBox tools are included.
Rootfs generation additionally requires e2fsprogs and squashfs-tools (Homebrew:
`brew install e2fsprogs squashfs`). If `mke2fs` or `debugfs` is unavailable, the
packer builds private e2fsprogs tools under `target/host-tools/`, using bundled
static UUID and blkid libraries. This avoids the MacPorts `libuuid`/`ossp-uuid`
conflict with erofs-utils. `make host-e2fsprogs` prepares these tools explicitly;
see [MacPorts setup](https://hyper-proj.github.io/HypeR/docs/learn/getting-started.html#macos-with-macports).
Run `make test-alpine-rootfs` for a disposable
root-disk boot and persistence test.

The build resolves the platform's qualified main channel to an immutable GHCR digest;
see [appliance requirements](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html#protocol-version-3-appliance-requirement).
Board deployment validates the v5 client-table resource contract. Use `IO_VM_PACKAGE`
when testing a local appliance before qualified publication.
When importing an uncached package, the fetcher uses ORAS from `PATH` or
automatically downloads ORAS 1.3.0 into `target/tools` for macOS/Linux on
ARM64/x86-64. Downloads require `curl` and are checked against pinned SHA-256
digests before installation. Set `IO_VM_ORAS` to use a specific executable.
Verified cached packages need neither ORAS nor network access.

The board bootstrap contains only the I/O VM's ITB. Ordinary guest images such
as Alpine are stored in the persistent data partition and loaded through `/data`;
they are not duplicated in the bootstrap ramfs. Standalone Native test archives
still carry their guest fixtures.

`BOARD_CONFIG` selects a custom JSON file, `BOARD_OUTPUT` selects staging, and
`BOARD_IMAGE` selects the resulting raw image. `board-image` refuses to
overwrite an existing image unless `BOARD_IMAGE_REPLACE=--replace` is supplied.
Use `make board-rebuild` for an explicit full replacement, or choose a new
output name to preserve the previous disk.
`rpi5-sd` defaults to `boards/rpi5.json`, honors an explicitly supplied
`BOARD_CONFIG`, and accepts a separate `RPI5_BOARD_CONFIG` override. Extra volume
artifacts supplied through `BOARD_ARTIFACTS` also reach the Pi image builder.
AArch64 `make run` selects this QEMU deployment profile. Build with `make` first;
`board-run` never creates a disk or rebuilds kernel/bootstrap. Existing images
must pass GPT validation against the selected configuration. QEMU uses the
kernel/bootstrap from the last build while persistent files and VM images remain
those stored on disk. See [updating existing deployments](#updating-existing-deployments)
before refreshing packaged guest artifacts.

### Native volume and placement policy

Plain `make`, `make rebuild` and `make run` keep only bootstrap in RAM and use
an independent EROFS system root. Stage-1 mounts that root temporarily at `/system`,
switches it to `/`, then starts formal init. The original archive stays at `/boot`
until formal providers adopt its live resources and bootstrap retires. The
persistent FAT volume is mounted at `/data` afterward. Namespace switching keeps
filesystem identity and cached contents; formal programs are never copied into
the boot archive. Default outputs are `target/board/qemu/` and
`target/board/rpi5-native/`. See [boot and system layout](https://hyper-proj.github.io/bootstrap/docs/boot-layout.html) for
component selection and handoff details.

`disk.partitions` owns the image layout. Each entry declares a unique `label`,
`size-mib` (8 MiB to 16 TiB), an optional explicit `uuid`, and optional
`filesystem`, `content`, or `image`. Without an explicit UUID the builder derives
one deterministically from the disk UUID and label. `content: "data"` populates
the persistent FAT payload; `content: "system"` populates the same-build system
programs. The data partition must be at least 64 MiB. An `image` names a
`BOARD_ARTIFACTS` input for an opaque guest disk or extra filesystem. Additional
FAT volumes can be formatted empty; additional EROFS volumes require an image.

`native.volumes` describes access, independently of image construction: each
entry has `source` (`PARTUUID=...` or `PARTLABEL=...`) and `read-only`.
`native.boot-fstab` describes bootstrap mounts, and `native.fstab` describes
mounts requested after formal init takes over. With no explicit `native` policy,
the builder selects filesystem partitions and generates `/system` in the boot
table and `/data` in the formal table from their content roles. An explicit policy
supplies both tables. Normal images require `native.layout: "split"` and an
independent system partition; embedded formal payloads are unsupported. User labels are not
reserved; `hyper-data` and `hyper-system` are defaults in the board files.

Guest board entries use `disk: {"source": "PARTLABEL=alpine"}` with an optional
`read-only` flag. Native clients allocate IDs from 8 down; guest clients allocate
from 1 up. These share eight data slots; client zero is management only. The
builder generates HypeR's `etc/hyper/storage.json`, broker admission table and
both fstabs. Linux receives only client IDs and network policy, never partition
selectors. Worker availability is checked when mounting, not by a build-time
provider dependency graph. FAT cannot preserve arbitrary system-payload symlinks.

### Updating existing deployments

Build outputs have distinct lifetimes:

| Artifact | Updated by ordinary `make` / `board-build` |
| --- | --- |
| Host kernel and bootstrap, including the I/O VM and its client policy | Yes |
| Generated EROFS root executables and libraries on an existing disk | Yes; only the system volume is refreshed |
| `/data/vms.json`, `/data/board.json` and guest FITs on an existing disk | No |
| Alpine's ext4 root, including `/init` and kernel modules | No |

The builder records the generated system payload hashes beside the disk.
An ordinary build refreshes changed EROFS contents after validating both GPT
copies and partition capacity. It stages a complete disk with all bytes outside
the system partition preserved, then publishes it atomically. This needs temporary
disk space for the replacement image. Stop QEMU before refreshing its disk.
An unchanged payload leaves the disk untouched. `make run` only checks the
payload record and asks for `make` when stale. Custom writable FAT system
volumes and changed GPT layouts require explicit replacement or a new output
directory; they are never overwritten by an automatic system refresh.

Changing a board's device policy also requires a matching disk deployment.
For example, adding networking to Alpine changes the bootstrap client table;
an old `/data/vms.json` that requests only a disk will be rejected by the broker.
The launcher's GPT check validates the disk layout, not the configuration or
guest files inside its partitions. See [startup diagnostics](https://hyper-proj.github.io/io-vm/docs/hyper-integration.html#startup-diagnostics).

To replace a disposable QEMU deployment with the current configuration and
guest files, run:

```sh
make board-rebuild ARCH=aarch64 BOARD=qemu
make board-run ARCH=aarch64 BOARD=qemu
```

This recreates the entire disk, including guest root filesystems. To retain an
existing disk, select a fresh `BOARD_OUTPUT` directory with `board-build` and
use that same directory with `board-run`. There is no automatic in-place
migration of existing guest files or VM definitions.

### Deployment and acceptance details

A partition's optional `image` names a disk artifact. The default board profiles
select `alpine-rootfs`, generated from the pinned Alpine rootfs and sized to the
volume. Custom inputs use `BOARD_ARTIFACTS="--artifact NAME=PATH"` and must
exactly match the declared volume size, preserving any guest backup GPT.
Without `image`, an opaque guest partition starts blank.

`make test-board-storage` creates an
isolated disk under `target/board-tests/` and boots it twice. Its test-only
application checks multi-megabyte file contents, extension gaps, timestamps,
rename, copy, directory enumeration and explicit synchronization. The second
cold boot checks persisted contents; both boots also exercise paced shell input
and, in stack-audit builds, require at least 2 KiB of measured stack reserve
and no more than 24 KiB of measured use (`STACK_MAXIMUM_USED`).
The fixture keeps its disk and logs for diagnosis and never uses `BOARD_IMAGE`
as a scratch disk.

`make test-boot-system ARCH=aarch64` uses a separate disposable disk to check
the formal root layout, supervision handoff, nested shells and tool capabilities,
VM lifecycle, and writable/read-only remounts. It then boots the same bootstrap
archive without storage controllers to verify the recovery shell. This test is
also repeated with a failed init handoff to verify recovery ownership and a
formal shell launched from recovery. It is included in
`sh tests/ci/run.sh board-storage`; it checks behavior, not performance.

`make test-storage-failure ARCH=aarch64` verifies Native recovery with a missing
controller, provider exit, closed or malformed readiness, the full readiness
timeout, and an invalid provider executable. It also checks the formal console
after early critical-manager failure, fatal supervision
after a critical manager exits from a ready formal environment, and provider
survival when the fleet file is absent. These are functional checks without performance
thresholds; they use separate bootstrap archives and no persistent board disk.

The bootstrap board document selects the physical controller explicitly through
`io-vm.io-device`: `profile` selects the I/O runtime's device policy and
one of `compatible`, `path`, or `pci-id` identifies the resource. QEMU defaults
to `pci-function` with `pci-id: "1af4:1048"` behind the host SMMUv3.
`virtio-mmio-scsi` with `virtio,mmio` remains supported for compatibility.
Multiple eligible matches are rejected;
use the full canonical FDT path when a machine exposes several controllers.
Claiming a device never falls back to another match because one is busy. Eight data clients are shared by Native volumes and business VMs: the default
split layout leaves six guest clients, while ramfs system placement leaves seven.
The finite fleet quota includes eight business VM allowances and one resident
I/O VM allowance, plus manager overhead. This is an admission ceiling, not
preallocated RAM or a guarantee that all eight guests fit the host.

For Pi 5, use `make rpi5-sd`
with the pinned official prebuilt DTBs/overlays. The boot chain uses the official
EEPROM firmware and its built-in BL31; no custom TF-A artifact is required.
QEMU and Pi 5 package channels advance automatically after qualified main builds. Recorded
Pi 5 development runs established SD-backed reads and basic guest persistence.
The 2026-10-06 [performance survey](https://github.com/HypeR-Proj/HypeR/blob/8d42d047058a2b58a291803700e86487fb723f7a/README.md#exploratory-pi-5-io-measurements)
also exercised Native and guest SD writes with full readback and physical guest
TCP traffic using the published pin. Full hardware qualification and DMA fault
recovery remain [deferred work](https://hyper-proj.github.io/HypeR/docs/project/roadmap.html#deferred-hardware-qualification).
The [manual test image](https://github.com/HypeR-Proj/ci/blob/main/hardware/storage/README.md) adds a 1 GiB workload
and enlarges the configuration volume to 2 GiB; default images neither compile
nor package that workload. The FAT boot
volume contains the firmware configuration, official DTBs/overlays, kernel and bootstrap archive.
The userspace `bcm2712-sdhci` profile validates the upstream C0/D0 SDIO1
resource graph: host/config registers, fixed clock, main/AON pinctrl and AON
GPIO supplies/card detection using the kernel's immutable firmware catalogue.
The board selects the full firmware node path,
because Wi-Fi SDIO2 has the same compatible string. Only named register bytes
are trapped through to hardware; adjacent firmware interrupt registers are not
mapped into Linux. The GPIO/pinctrl controllers are claimed exclusively against
other HypeR assignments, and card detection keeps the upstream polling fallback.
The kernel atomically claims the requested register bundle and level interrupt;
it does not interpret SDHCI registers, clocks, pinmux or GPIO policy. A separate
I/O runtime worker handles trapped MMIO and physical IRQ notifications, so the
main runtime can wait for Linux storage without blocking its device service.
The worker reads SDHCI status and signal-enable registers to update the guest
interrupt level. Native calls 143–147 provide firmware reads, bundle claims,
bounded 1/2/4-byte MMIO, pending IRQ tokens and sequence-checked IRQ completion.

The profile preserves noncoherent DMA and requires the controller's actual
64-bit DMA capability; there is no implicit 32-bit bounce pool. Device retirement
currently cannot prove SD DMA has stopped: after the I/O VM has run, stopping it
quarantines its device and all own/imported memory until host reboot. A timeout
never releases potentially active DMA storage. These are explicit deployment
constraints, not a claim of successful hardware qualification. The common
appliance must include the upstream SDHCI/GPIO/pinctrl/regulator drivers, and the
[Pi 5 bring-up checks](https://github.com/HypeR-Proj/kernel/blob/main/docs/usage/rpi5.md) remain required.

The same `boards/rpi5.json` deployment gives Alpine an RP1 GEM network uplink
alongside its SD-backed disk. The kernel assigns the whole RP1 PCI function
while retaining host PCIe transport, BAR ownership and MSI routing. Upstream
Linux owns RP1 interrupts, GEM, clocks, GPIO and the Ethernet bridge. Its DMA
bus translation is separate from SDHCI. This trusted I/O VM path has no IOMMU
DMA isolation. The pinned Pi 5 appliance has served the physical TCP survey;
extended network qualification remains deferred. See
[Ethernet bring-up](https://github.com/HypeR-Proj/kernel/blob/main/docs/usage/rpi5.md#ethernet-backend-qualification).

`make test-userspace-device` uses a test-only virtio worker and a real QEMU disk
with a level interrupt to exercise the same generic bundle/MMIO/IRQ mechanism.
This is a validation fixture, not evidence that Pi 5 SDIO or DMA has passed
hardware qualification.

The I/O control loop keeps at most one pending backend transaction per client
and advances clients independently. Absolute deadlines are checked even when
unrelated objects remain ready. Losing the manager admission endpoint closes
new admissions without stopping existing clients or `/data`. A request already
sent to Linux must receive its matching reply, or the I/O VM must be safely
retired, before its DMA mapping can be reclaimed.

`make test-board-business` exercises ordinary scatter-backed VM start, stop,
and restart against a real QEMU disk partition. `make test-board-broker` uses
separately built test applications to withhold client A's reply after its real
HELLO reaches Linux. Client B must complete RESET, RELEASE, kernel-proven mapping
retirement, and notification disconnection before A can receive that reply.
The fixture checks client and generation identities, closes the actual manager
admission endpoint, and verifies both VM disks and `/data`.
Production applications are never rebuilt with these fault-injection features.

VM runtime settings live in each board `virtual-machines` entry's required
`configuration` object (`vcpus`, `memory-bytes`, `bootargs`, plus optional
`affinity`). For example, `"affinity": [{"vcpu": 0, "cpus": [1, 3]}]` allows
vCPU 0 to run on host CPU 1 or 3; omitted entries retain normal scheduler
placement. The packer
copies it unchanged into `/data/vms.json`.

The resident I/O VM has its own `io-vm` node, outside `virtual-machines`:

```json
"io-vm": {
  "runtime": "io-runtime",
  "name": "oi",
  "image": "/vm/storage/oi.itb",
  "configuration": {
    "memory-bytes": 67108864,
    "vcpus": 1,
    "bootargs": "console=ttyAMA0 rdinit=/init hyper.role=io hyper.mode=standby",
    "affinity": [{"vcpu": 0, "cpus": [1, 3]}]
  },
  "io-device": {
    "profile": "pci-function",
    "pci-id": "1af4:1048"
  }
}
```

`runtime` assigns this node to io-runtime. Its `configuration` uses the same
schema as an ordinary VM; the resident runtime currently supports exactly
64 MiB and one vCPU. `name` is an ordinary VM name. `image` selects a canonical
absolute `.itb` path beneath `/vm/` in the bootstrap archive, available before
`/data` mounts. The packer places the generated ITB at this path, and io-runtime
opens that same path from `/etc/hyper/board.json`. No separate I/O VM fleet
configuration is generated for deployment. Name, image path and placement in
`vmm status NAME` come from the runtime's observation reply.

Storage mode packages the board settings unchanged. Standby and diskless Pi 5
bring-up derive their settings from `BOARD_CONFIG`, preserving name, image,
affinity and memory while removing the storage-volume requirement and selecting
the corresponding `hyper.mode`. Bring-up is an explicit diagnostic mode: it
registers the configured name with the ordinary VM manager for manual startup,
without physical-device authority. ITBs contain payloads, not these settings.
