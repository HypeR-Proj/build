# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Board deployment, external appliances and storage acceptance.
# Linux images are imported from the independent appliance repository.
.PHONY: io-vm-fetch
io-vm-fetch:
	python3 -B scripts/fetch-io-vm.py --reference "$(IO_VM_REFERENCE)" --platform "$(IO_VM_PLATFORM)"

# Ordinary shell/services plus a single idle Linux storage backend.
.PHONY: io-initramfs
io-initramfs: APP_BUILD_PROFILE = $(NATIVE_IMAGE_PROFILE)
io-initramfs: app fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "I/O VM run profile requires aarch64" >&2; exit 2; }
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform qemu \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	python3 -B scripts/io-vm-images.py --package "$$package" \
		--board "$(BOARD_CONFIG)" --mode standby \
		--fit-pack "$(FIT_PACK)" --output "$(APP_OUTPUT)/io-standby.itb"
	$(MAKE) -o app native-initramfs \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/initramfs-io.cpio" \
		NATIVE_SERVICE_MANIFEST="$(CURDIR)/bootstrap/stage1/init/config/services-with-vms.json" \
		NATIVE_VM_CONFIG="$(CURDIR)/bootstrap/stage1/init/tests/config/vms-io.json" \
		NATIVE_ENTRY_MANIFEST="$(APP_OUTPUT)/io-standby.entries.json"

.PHONY: test-io-standby
test-io-standby: image io-initramfs
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-io-standby.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" \
		--initramfs "$(APP_OUTPUT)/initramfs-io.cpio" \
		--log "$(APP_OUTPUT)/io-standby-$(QEMU_CPUS).log"

# A diskless hardware diagnostic boots static stage1 and its fallback shell only.
.PHONY: recovery-initramfs rpi5-bringup rpi5-bringup-deployment
recovery-initramfs: app-boot $(NEWC_PACK)
	python3 -B scripts/pack-native-system.py --recovery-only \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--output "$(NATIVE_INITRAMFS).deployment" --initramfs "$(NATIVE_INITRAMFS)" \
		--deployment "$(APP_DEPLOYMENT)" --profile system \
		$(SYSTEM_CONFIG_ARGS) \
		--apps "$(APP_OUTPUT)" --boot-apps "$(APP_BOOT_OUTPUT)" --sdk "$(SDK_OUTPUT)" \
		--std "$(NATIVE_STD_TEST_OUTPUT)" --arch "$(NATIVE_ARCH)" \
		--boot-components $(APP_BOOT_COMPONENTS) \
		--services "$(CURDIR)/bootstrap/stage1/init/config/services-console-only.json"

rpi5-bringup-deployment:
	python3 -B scripts/prepare-native-layout.py --board "$(CURDIR)/boards/rpi5-native.json" \
		--recovery --output "$(RPI5_BRINGUP_OUTPUT)/bringup-board.json"

rpi5-bringup: image rpi5-bringup-deployment
	@test "$(ARCH)" = aarch64 || { echo "Pi 5 requires ARCH=aarch64" >&2; exit 2; }
	$(MAKE) recovery-initramfs APP_BOOT_COMPONENTS="init console-raw fallback-sh" \
		NATIVE_INITRAMFS="$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio"
	python3 -B scripts/rpi5-bringup.py $(if $(RPI5_BOOT_PACKAGE),--package "$(RPI5_BOOT_PACKAGE)",) \
		--recovery --board "$(RPI5_BRINGUP_OUTPUT)/bringup-board.json" \
		--kernel "$(KERNEL_IMAGE)" --initramfs "$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio" \
		--output "$(RPI5_BRINGUP_OUTPUT)/disk.img" $(BOARD_IMAGE_REPLACE)

# Complete Pi 5 deployment with SD storage and the RP1 Ethernet uplink.
.PHONY: rpi5-sd
rpi5-sd: image
	@test "$(ARCH)" = aarch64 || { echo "Pi 5 requires ARCH=aarch64" >&2; exit 2; }
	$(MAKE) board-initramfs board-guest-images BOARD=rpi5 BOARD_CONFIG="$(RPI5_BOARD_CONFIG)" \
		BOARD_OUTPUT="$(RPI5_BRINGUP_OUTPUT)" NATIVE_IMAGE_PROFILE=system
	python3 -B scripts/rpi5-bringup.py $(if $(RPI5_BOOT_PACKAGE),--package "$(RPI5_BOOT_PACKAGE)",) \
		--board "$(RPI5_BRINGUP_OUTPUT)/deployment.json" --kernel "$(KERNEL_IMAGE)" \
		--initramfs "$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio" \
		--output "$(RPI5_BRINGUP_OUTPUT)/disk.img" $(BOARD_IMAGE_REPLACE) \
		--system-root "$(RPI5_BRINGUP_OUTPUT)/system-root" --mkfs-erofs "$(EROFS_MKFS)" \
		--default-artifact "alpine=$(RPI5_BRINGUP_OUTPUT)/alpine.itb" \
		--default-artifact "alpine-rootfs=$(RPI5_BRINGUP_OUTPUT)/alpine.ext4" $(BOARD_ARTIFACTS)

.PHONY: board-deployment
board-deployment:
	python3 -B scripts/prepare-native-layout.py --board "$(BOARD_CONFIG)" \
		--output "$(BOARD_DEPLOYMENT)"

.PHONY: board-plan board-initramfs board-image board-build board-rebuild board-run board-guest-images
board-plan: board-deployment
	python3 -B scripts/pack-board-image.py --board "$(BOARD_DEPLOYMENT)" --plan

board-initramfs: board-deployment app app-boot fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "board deployment requires aarch64" >&2; exit 2; }
	python3 -B scripts/board_bootstrap.py --board "$(BOARD_DEPLOYMENT)" --output "$(BOARD_OUTPUT)/board"
	@needed=$$(python3 -B scripts/boot_profiles.py needs-io-vm --board "$(BOARD_DEPLOYMENT)" $(SYSTEM_CONFIG_ARGS)) || exit $$?; \
	if test "$$needed" = no; then \
		echo "Bootstrap profiles do not require an I/O VM image."; \
	else \
	package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform "$(BOARD)" \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	python3 -B scripts/io-vm-images.py --package "$$package" \
		--fit-pack "$(FIT_PACK)" --board "$(BOARD_DEPLOYMENT)" --output "$(BOARD_OUTPUT)/io.itb"; \
	fi
	python3 -B scripts/pack-native-system.py \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--deployment "$(APP_DEPLOYMENT)" --arch "$(NATIVE_ARCH)" \
		$(SYSTEM_CONFIG_ARGS) \
		--apps "$(APP_OUTPUT)" --boot-apps "$(APP_BOOT_OUTPUT)" \
		--sdk "$(SDK_OUTPUT)" --std "$(NATIVE_STD_TEST_OUTPUT)" \
		--boot-components $(APP_BOOT_COMPONENTS) \
		--board "$(BOARD_DEPLOYMENT)" --output "$(BOARD_OUTPUT)" --board-output "$(BOARD_OUTPUT)/board" \
		$(BOARD_EXTRA_ENTRIES)

# QEMU loads kernel/bootstrap directly from the host. Refresh these on every
# build, refresh the generated EROFS volume, and seed persistent data only once.
board-build: image board-initramfs
	@if test ! -e "$(BOARD_IMAGE)" && test ! -L "$(BOARD_IMAGE)"; then \
		$(MAKE) -o image -o board-initramfs board-image; \
	fi
	$(MAKE) board-update-system

# Explicit full repacking resets persistent volumes only after successful packing.
board-rebuild: image board-initramfs board-guest-images
	@echo "Rebuilding $(BOARD_IMAGE): disk data will be reset after successful packing."
	$(MAKE) -o image -o board-initramfs -o board-guest-images board-image BOARD_IMAGE_REPLACE=--replace

board-guest-images: board-deployment
	@artifacts=$$(python3 -B scripts/prepare-native-layout.py --board "$(BOARD_DEPLOYMENT)" --artifacts) || exit $$?; \
	case " $$artifacts " in *' alpine '*|*' alpine-rootfs '*) ;; *) exit 0 ;; esac; \
	$(MAKE) guest-itb && \
	python3 -B scripts/pack-guest-disk.py --board "$(BOARD_DEPLOYMENT)" \
		--rootfs "$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/rootfs.tar" --output "$(BOARD_OUTPUT)/alpine.ext4"
	@artifacts=$$(python3 -B scripts/prepare-native-layout.py --board "$(BOARD_DEPLOYMENT)" --artifacts) || exit $$?; \
	case " $$artifacts " in *' alpine '*) ;; *) exit 0 ;; esac; \
	"$(FIT_PACK)" "$(BOARD_OUTPUT)/alpine.itb" "$(NATIVE_GUEST_ARCH)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/Image" "$(NATIVE_GUEST_LOAD)" "$(NATIVE_GUEST_LOAD)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/initramfs.cpio.gz"

board-image: image board-initramfs board-guest-images
	python3 -B scripts/pack-board-image.py --board "$(BOARD_DEPLOYMENT)" --output "$(BOARD_IMAGE)" $(BOARD_IMAGE_REPLACE) \
		--system-root "$(BOARD_OUTPUT)/system-root" --mkfs-erofs "$(EROFS_MKFS)" \
		--default-artifact "hyper=$(KERNEL_IMAGE)" --default-artifact "bootstrap=$(BOARD_OUTPUT)/bootstrap.cpio" \
		--default-artifact "alpine=$(BOARD_OUTPUT)/alpine.itb" \
		--default-artifact "alpine-rootfs=$(BOARD_OUTPUT)/alpine.ext4" $(BOARD_ARTIFACTS)

.PHONY: board-update-system board-check-system
board-update-system:
	python3 -B scripts/pack-board-image.py --board "$(BOARD_DEPLOYMENT)" \
		--output "$(BOARD_IMAGE)" --system-root "$(BOARD_OUTPUT)/system-root" \
		--mkfs-erofs "$(EROFS_MKFS)" --refresh-system-payload

board-check-system:
	@test -f "$(BOARD_DEPLOYMENT)" -a -f "$(BOARD_IMAGE)" -a -d "$(BOARD_OUTPUT)/system-root" || { \
		echo "Deployment artifacts are missing; run make first." >&2; exit 2; \
	}
	python3 -B scripts/pack-board-image.py --board "$(BOARD_DEPLOYMENT)" \
		--output "$(BOARD_IMAGE)" --system-root "$(BOARD_OUTPUT)/system-root" --check-system-payload

board-run: board-check-system
	@test "$(BOARD)" = qemu || { echo "board-run requires the QEMU deployment profile" >&2; exit 2; }
	$(NATIVE_QEMU_ENV) python3 -B scripts/run-io-vm.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$(BOARD_OUTPUT)/bootstrap.cpio" \
		--disk "$(BOARD_IMAGE)" --board "$(BOARD_DEPLOYMENT)"

# Generate the trimmed acceptance selection from the test workload, without
# maintaining a second production preset. The reporter remains test-only.
.PHONY: test-product-config test-product-config-run
test-product-config:
	python3 -B tests/qemu/verify-product-config.py prepare \
		--output "$(BOARD_TEST_OUTPUT)/product-config/product.config"
	$(MAKE) test-product-config-run BOARD=qemu \
		SYSTEM_CONFIG="$(abspath $(BOARD_TEST_OUTPUT))/product-config/product.config" \
		BOARD_CONFIG="$(CURDIR)/boards/qemu-minimal.json" \
		BOARD_OUTPUT="$(abspath $(BOARD_TEST_OUTPUT))/product-config"

test-product-config-run: board-build
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-product-config.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$(BOARD_OUTPUT)/bootstrap.cpio" \
		--disk "$(BOARD_IMAGE)" --board "$(BOARD_DEPLOYMENT)" \
		--system-config "$(SYSTEM_CONFIG)" --log "$(BOARD_OUTPUT)/accept.log"

.PHONY: test-board-storage
test-board-storage: app image
	@test "$(ARCH)" = aarch64 || { echo "board storage acceptance requires aarch64" >&2; exit 2; }
	$(MAKE) -o app-fetch app APP_OUTPUT="$(BOARD_TEST_OUTPUT)/storage-apps" \
		APP_FEATURES=hyper-io-runtime/storage-probe,hyper-fs-fat/block-queue-test,hyper-fs-fat-system/block-queue-test APP_EXTRA_BINS=hyper-storage-probe
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/run.XXXXXX") && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		APP_OUTPUT="$(BOARD_TEST_OUTPUT)/storage-apps" \
		APP_FEATURES=hyper-io-runtime/storage-probe,hyper-fs-fat/block-queue-test,hyper-fs-fat-system/block-queue-test \
		BOARD_CONFIG="$(BOARD_CONFIG)" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" \
		BOARD_EXTRA_ENTRIES='0755 bin/storage-probe "$(BOARD_TEST_OUTPUT)/storage-apps/storage-probe"' && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-board-storage.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$(BOARD_CONFIG)" --log "$$fixture/accept" \
		--require-block-queue \
		$(if $(filter 1,$(STACK_AUDIT)),--minimum-stack-remaining "$(STACK_MINIMUM_REMAINING)" --maximum-stack-used "$(STACK_MAXIMUM_USED)")

# Formal-root and recovery acceptance own a disposable disk and reuse only
# their bootstrap archive for the controller-free boot.
.PHONY: test-boot-system
test-boot-system: APP_EXTRA_BINS += hyper-fs-worker-probe hyper-test-report
test-boot-system: app image
	@test "$(ARCH)" = aarch64 || { echo "boot/system acceptance requires aarch64" >&2; exit 2; }
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/boot-system.XXXXXX") && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		BOARD_CONFIG="$(CURDIR)/boards/qemu.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-boot-system.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/deployment.json" \
		--log "$$fixture/deployment.log" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-boot-system.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--recovery --log "$$fixture/recovery.log" && \
	python3 -B tests/qemu/verify-boot-system.py prepare-handoff \
		--entries "$$fixture/native-system/boot.entries.json" \
		--output "$$fixture/handoff/bootstrap.cpio" --packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-boot-system.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/handoff/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/deployment.json" \
		--failed-handoff --log "$$fixture/handoff.log" && \
	for case in missing-io-runtime missing-fs-erofs; do \
		python3 -B tests/qemu/verify-boot-system.py prepare-provider-rejection \
			--case "$$case" --system-root "$$fixture/system-root" \
			--board "$$fixture/deployment.json" --output "$$fixture/$$case" \
			--mkfs-erofs "$(EROFS_MKFS)" \
			--artifact "alpine=$$fixture/alpine.itb" \
			--artifact "alpine-rootfs=$$fixture/alpine.ext4" && \
		$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-boot-system.py \
			--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
			--disk "$$fixture/$$case/disk.img" --board "$$fixture/deployment.json" \
			--provider-case "$$case" --log "$$fixture/$$case/accept.log" || exit $$?; \
	done && \
	python3 -B tests/qemu/verify-fs-worker-supervision.py prepare \
		--system-root "$$fixture/system-root" --board "$$fixture/deployment.json" \
		--output "$$fixture/worker-failure" --mkfs-erofs "$(EROFS_MKFS)" \
		--worker "$(APP_OUTPUT)/fs-worker-probe" --reporter "$(APP_OUTPUT)/test-report" \
		--artifact "alpine=$$fixture/alpine.itb" \
		--artifact "alpine-rootfs=$$fixture/alpine.ext4" && \
	for case in exit fault stop-fault; do \
		$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-fs-worker-supervision.py run \
			--case "$$case" --qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" \
			--initramfs "$$fixture/bootstrap.cpio" --disk "$$fixture/worker-failure/disk.img" \
			--board "$$fixture/deployment.json" --log "$$fixture/worker-failure/$$case.log" || exit $$?; \
	done

.PHONY: test-board-business
test-board-business: app image fit-pack
	@test "$(ARCH)" = aarch64 || { echo "board business acceptance requires aarch64" >&2; exit 2; }
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform qemu \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/business.XXXXXX") && \
	python3 -B tests/qemu/verify-board-business.py prepare --package "$$package" \
		--fit-pack "$(FIT_PACK)" --output "$$fixture" && \
	$(MAKE) -o image -o app board-image BOARD=qemu IO_VM_PACKAGE="$$package" \
		BOARD_CONFIG="$$fixture/config.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" BOARD_ARTIFACTS="--artifact business=$$fixture/business.itb" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-board-business.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/config.json" --log "$$fixture/accept.log"

# Each invocation owns a fresh disk; networking never uses the deployment disk.
.PHONY: test-board-network
test-board-network: app image
	@test "$(ARCH)" = aarch64 || { echo "board network acceptance requires aarch64" >&2; exit 2; }
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/network.XXXXXX") && \
	python3 -B tests/qemu/verify-network.py prepare \
		--board "$(CURDIR)/boards/qemu.json" --output "$$fixture/config.json" \
		--virtio-transport "$(or $(NETWORK_VIRTIO_TRANSPORT),pci)" \
		--physical-transport "$(or $(NETWORK_PHYSICAL_TRANSPORT),pci)" && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		BOARD_CONFIG="$$fixture/config.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-network.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/config.json" --log "$$fixture/network.log" \
		$(if $(NETWORK_TX_OFFLOAD),--expect-tx-offload "$(NETWORK_TX_OFFLOAD)")

# Dedicated legacy transport coverage: real physical MMIO block/network,
# bootstrap handoff, guest I/O, endpoint reset and repeated grant retirement.
.PHONY: test-board-mmio
test-board-mmio:
	$(MAKE) test-board-network NETWORK_PHYSICAL_TRANSPORT=mmio NETWORK_VIRTIO_TRANSPORT=mmio

# Revoke a live physical NIC RX page in a disposable, unmodified Linux I/O VM.
# The test hook is absent from production images and exposes no Native control API.
.PHONY: test-board-dma-revoke
test-board-dma-revoke:
	@test "$(ARCH)" = aarch64 || { echo "DMA revocation acceptance requires aarch64" >&2; exit 2; }
	$(CARGO) test --manifest-path kernel/tests/host/Cargo.toml \
		--features kernel-test,io-dma-revoke-test test_revocation
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/dma-revoke.XXXXXX") && \
	build="$(abspath $(BOARD_TEST_OUTPUT))/dma-revoke-kernel" && \
	CARGO_TARGET_DIR="$$build" $(MAKE) -C "$(KERNEL_DIRECTORY)" image \
		$(SYSTEM_KERNEL_ARGS) \
		KERNEL_BUILD_ROOT="$$build" \
		CONFIG_FILE="$(if $(filter undefined,$(origin CONFIG_FILE)),$(KERNEL_DIRECTORY)/configs/qemu_aarch64_defconfig,$(KERNEL_CONFIG_PATH))" \
		KERNEL_OUTPUT="$$build/$(KERNEL_TARGET)/$(KERNEL_PROFILE)" \
		KALLSYMS_SEED_TARGET="$$build/kallsyms-seed" \
		CARGO_FEATURES='--features kernel-test,io-dma-revoke-test' \
		&& cp "$$build/$(KERNEL_TARGET)/$(KERNEL_PROFILE)/hyper.img" "$$fixture/hyper.img" && \
	$(MAKE) -o image board-image BOARD=qemu APP_EXTRA_BINS=hyper-test-report \
		KERNEL_IMAGE="$$fixture/hyper.img" \
		BOARD_CONFIG="$(CURDIR)/boards/qemu.json" BOARD_OUTPUT="$$fixture" \
		APP_OUTPUT="$$fixture/apps" BOARD_IMAGE="$$fixture/disk.img" \
		BOARD_EXTRA_ENTRIES="0755 system:bin/test-report $$fixture/apps/test-report" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-io-dma-revoke.py \
		--qemu "$(QEMU)" --image "$$fixture/hyper.img" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/deployment.json" --log "$$fixture/accept.log" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-io-dma-revoke.py --before-adoption \
		--qemu "$(QEMU)" --image "$$fixture/hyper.img" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/deployment.json" --log "$$fixture/before-adoption.log"

# Fault injection is compiled into a separate output tree; normal app artifacts
# and subsequent make run images never inherit the test features.
.PHONY: test-io-console-hotplug
test-io-console-hotplug: image
	@test "$(ARCH)" = aarch64 || { echo "console hotplug acceptance requires aarch64" >&2; exit 2; }
	@test -n "$(IO_VM_PACKAGE)" -a -d "$(IO_VM_PACKAGE)" || { echo "set IO_VM_PACKAGE to an appliance with PCIe hotplug and virtio-console support" >&2; exit 2; }
	@test -n "$(CONSOLE_HOTPLUG_PROBE)" -a -f "$(CONSOLE_HOTPLUG_PROBE)" || { echo "set CONSOLE_HOTPLUG_PROBE to the statically linked AArch64 Linux tests/qemu/guest/console-hotplug.c executable" >&2; exit 2; }
	$(MAKE) app APP_OUTPUT="$(BOARD_TEST_OUTPUT)/console-apps" \
		APP_FEATURES=hyper-io-runtime-system/console-test
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/console-hotplug.XXXXXX") && \
	python3 -B tests/qemu/verify-console-hotplug.py prepare --package "$(IO_VM_PACKAGE)" \
		--probe "$(CONSOLE_HOTPLUG_PROBE)" --board "$(CURDIR)/boards/qemu.json" \
		--output "$$fixture/prepared" && \
	$(MAKE) -o image -o app board-image BOARD=qemu IO_VM_PACKAGE="$$fixture/prepared/package" \
		APP_OUTPUT="$(BOARD_TEST_OUTPUT)/console-apps" APP_FEATURES=hyper-io-runtime-system/console-test \
		BOARD_CONFIG="$$fixture/prepared/config.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-console-hotplug.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/prepared/config.json" \
		--log "$$fixture/accept.log"

.PHONY: test-board-broker
test-board-broker: app image fit-pack
	@test "$(ARCH)" = aarch64 || { echo "broker acceptance requires aarch64" >&2; exit 2; }
	$(MAKE) -o app-fetch app APP_OUTPUT="$(BOARD_TEST_OUTPUT)/broker-apps" \
		APP_FEATURES=hyper-io-runtime-system/broker-test,hyper-vm-manager/broker-test,hyper-vm-runtime/broker-test
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform qemu \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/broker.XXXXXX") && \
	python3 -B tests/qemu/verify-board-business.py prepare --package "$$package" \
		--fit-pack "$(FIT_PACK)" --output "$$fixture" && \
	python3 -B tests/qemu/verify-board-broker.py prepare --board "$$fixture/config.json" && \
	$(MAKE) -o image -o app board-image BOARD=qemu IO_VM_PACKAGE="$$package" \
		APP_OUTPUT="$(BOARD_TEST_OUTPUT)/broker-apps" \
		APP_FEATURES=hyper-io-runtime-system/broker-test,hyper-vm-manager/broker-test,hyper-vm-runtime/broker-test \
		BOARD_CONFIG="$$fixture/config.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" BOARD_ARTIFACTS="--artifact business=$$fixture/business.itb" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-board-broker.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/config.json" --log "$$fixture/accept.log"

# The test backend uses real QEMU virtio-scsi through the same generic Native
# MMIO/IRQ interfaces as SDHCI. No test feature enters ordinary app artifacts.
.PHONY: test-userspace-device
test-userspace-device: app image
	@test "$(ARCH)" = aarch64 || { echo "userspace physical devices require aarch64" >&2; exit 2; }
	$(MAKE) -o app-fetch app APP_OUTPUT="$(BOARD_TEST_OUTPUT)/device-apps" \
		APP_FEATURES=hyper-io-runtime/storage-probe,hyper-io-runtime/userspace-device-test,hyper-io-runtime-system/userspace-device-test \
		APP_EXTRA_BINS=hyper-storage-probe
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/userspace-device.XXXXXX") && \
	python3 -B tests/qemu/prepare-userspace-device.py --qemu "$(QEMU)" \
		--board "$(CURDIR)/boards/qemu.json" --output "$$fixture" && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		APP_OUTPUT="$(BOARD_TEST_OUTPUT)/device-apps" \
		APP_FEATURES=hyper-io-runtime/storage-probe,hyper-io-runtime/userspace-device-test,hyper-io-runtime-system/userspace-device-test \
		BOARD_CONFIG="$$fixture/config.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" \
		BOARD_EXTRA_ENTRIES='0755 bin/storage-probe "$(BOARD_TEST_OUTPUT)/device-apps/storage-probe"' && \
	QEMU_DTB="$$fixture/host.dtb" QEMU_MACHINE=virt,virtualization=on,gic-version=3 \
		QEMU_CPU=max QEMU_CPUS=4 QEMU_MEMORY=512M \
		python3 -B tests/qemu/verify-board-storage.py --qemu "$(QEMU)" \
		--image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$$fixture/config.json" --log "$$fixture/accept" \
		--require-userspace-device \
		$(if $(filter 1,$(STACK_AUDIT)),--minimum-stack-remaining "$(STACK_MINIMUM_REMAINING)" --maximum-stack-used "$(STACK_MAXIMUM_USED)")

.PHONY: test-alpine-rootfs
test-alpine-rootfs: app image
	@test "$(ARCH)" = aarch64 || { echo "Alpine board rootfs acceptance requires aarch64" >&2; exit 2; }
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/alpine.XXXXXX") && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		BOARD_CONFIG="$(CURDIR)/boards/qemu.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-alpine-rootfs.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" \
		--initramfs "$$fixture/bootstrap.cpio" --disk "$$fixture/disk.img" \
		--board "$(CURDIR)/boards/qemu.json" --log "$$fixture/accept.log"
