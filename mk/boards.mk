# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Board deployment, external appliances and storage acceptance.
# Linux images are imported from the independent appliance repository.
.PHONY: io-vm-fetch
io-vm-fetch:
	python3 -B scripts/fetch-io-vm.py --reference "$(IO_VM_REFERENCE)" --platform "$(IO_VM_PLATFORM)"

# Ordinary shell/services plus a single idle Linux storage backend.
.PHONY: io-initramfs
io-initramfs: app fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "I/O VM run profile requires aarch64" >&2; exit 2; }
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform qemu \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	python3 -B scripts/io-vm-images.py --package "$$package" \
		--fit-pack "$(FIT_PACK)" --output "$(APP_OUTPUT)/io-standby.itb"
	$(MAKE) -o app native-initramfs \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/initramfs-io.cpio" \
		NATIVE_SERVICE_MANIFEST="$(CURDIR)/app/init/config/services.json" \
		NATIVE_VM_CONFIG="$(CURDIR)/app/init/tests/config/vms-io.json" \
		NATIVE_EXTRA_ENTRIES='0755 svc/io-runtime "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-io-runtime" 0644 vm/io.itb "$(APP_OUTPUT)/io-standby.itb"'

.PHONY: test-io-standby
test-io-standby: image io-initramfs
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-io-standby.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" \
		--initramfs "$(APP_OUTPUT)/initramfs-io.cpio" \
		--log "$(APP_OUTPUT)/io-standby-$(QEMU_CPUS).log"

# Native-only hardware qualification does not download or start Linux guests.
.PHONY: rpi5-bringup
rpi5-bringup: image
	@test "$(ARCH)" = aarch64 || { echo "Pi 5 requires ARCH=aarch64" >&2; exit 2; }
	$(MAKE) native-initramfs NATIVE_IMAGE_PROFILE=system NATIVE_GUEST_PREREQUISITES= NATIVE_GUEST_ENTRY= \
		NATIVE_SERVICE_MANIFEST="$(CURDIR)/app/init/config/native/services.json" \
		NATIVE_VM_CONFIG="$(CURDIR)/app/init/config/native/vms.json" \
		NATIVE_INITRAMFS="$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio"
	python3 -B scripts/rpi5-bringup.py $(if $(RPI5_BOOT_PACKAGE),--package "$(RPI5_BOOT_PACKAGE)",) \
		--kernel "$(KERNEL_IMAGE)" --initramfs "$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio" \
		--output "$(RPI5_BRINGUP_OUTPUT)/disk.img" $(BOARD_IMAGE_REPLACE)

# Diskless Linux appliance qualification; no /data or device assignment.
.PHONY: rpi5-io-bringup
rpi5-io-bringup: image app fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "Pi 5 requires ARCH=aarch64" >&2; exit 2; }
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform rpi5 \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	python3 -B scripts/io-vm-images.py --package "$$package" --platform rpi5 --bringup \
		--fit-pack "$(FIT_PACK)" --output "$(RPI5_BRINGUP_OUTPUT)/io.itb"
	$(MAKE) -o app native-initramfs NATIVE_IMAGE_PROFILE=system NATIVE_GUEST_PREREQUISITES= NATIVE_GUEST_ENTRY= \
		NATIVE_SERVICE_MANIFEST="$(RPI5_BRINGUP_OUTPUT)/bringup/services.json" \
		NATIVE_VM_CONFIG="$(RPI5_BRINGUP_OUTPUT)/bringup/vms.json" \
		NATIVE_EXTRA_ENTRIES='0644 vm/io.itb "$(RPI5_BRINGUP_OUTPUT)/io.itb"' \
		NATIVE_INITRAMFS="$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio"
	python3 -B scripts/rpi5-bringup.py $(if $(RPI5_BOOT_PACKAGE),--package "$(RPI5_BOOT_PACKAGE)",) \
		--kernel "$(KERNEL_IMAGE)" --initramfs "$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio" \
		--output "$(RPI5_BRINGUP_OUTPUT)/disk.img" $(BOARD_IMAGE_REPLACE)

# Physical SD backend with the same minimal Alpine root disk as QEMU.
.PHONY: rpi5-sd
rpi5-sd: image
	@test "$(ARCH)" = aarch64 || { echo "Pi 5 requires ARCH=aarch64" >&2; exit 2; }
	$(MAKE) board-initramfs board-guest-images BOARD=rpi5 BOARD_CONFIG="$(CURDIR)/boards/rpi5-sd.json" \
		BOARD_OUTPUT="$(RPI5_BRINGUP_OUTPUT)" NATIVE_IMAGE_PROFILE=system
	python3 -B scripts/rpi5-bringup.py $(if $(RPI5_BOOT_PACKAGE),--package "$(RPI5_BOOT_PACKAGE)",) \
		--board "$(CURDIR)/boards/rpi5-sd.json" --kernel "$(KERNEL_IMAGE)" \
		--initramfs "$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio" \
		--output "$(RPI5_BRINGUP_OUTPUT)/disk.img" $(BOARD_IMAGE_REPLACE) \
		--artifact "alpine=$(RPI5_BRINGUP_OUTPUT)/alpine.itb" \
		--artifact "alpine-rootfs=$(RPI5_BRINGUP_OUTPUT)/alpine.ext4"

.PHONY: board-plan board-initramfs board-image board-build board-rebuild board-run board-guest-images
board-plan:
	python3 -B scripts/pack-board-image.py --board "$(BOARD_CONFIG)" --plan

board-initramfs: app fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "board deployment requires aarch64" >&2; exit 2; }
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform "$(BOARD)" \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	python3 -B scripts/io-vm-images.py --package "$$package" \
		--fit-pack "$(FIT_PACK)" --board "$(BOARD_CONFIG)" --output "$(BOARD_OUTPUT)/io.itb"
	$(MAKE) -o app native-initramfs \
		NATIVE_INITRAMFS="$(BOARD_OUTPUT)/bootstrap.cpio" \
		NATIVE_GUEST_PREREQUISITES= NATIVE_GUEST_ENTRY= \
		NATIVE_SERVICE_MANIFEST="$(BOARD_OUTPUT)/board/services.json" \
		NATIVE_VM_CONFIG="$(BOARD_OUTPUT)/board/vms.json" \
		NATIVE_EXTRA_ENTRIES='0755 svc/io-runtime "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-io-runtime" 0644 vm/io.itb "$(BOARD_OUTPUT)/io.itb" 0644 etc/hyper/board.json "$(BOARD_OUTPUT)/board/board.json" 0644 etc/hyper/io-clients.conf "$(BOARD_OUTPUT)/board/io-clients.conf" $(BOARD_EXTRA_ENTRIES)'

# QEMU loads kernel/bootstrap directly from the host. Refresh these on every
# build, but populate persistent volumes only when creating the first disk.
board-build: image board-initramfs
	@if test ! -e "$(BOARD_IMAGE)" && test ! -L "$(BOARD_IMAGE)"; then \
		$(MAKE) -o image -o board-initramfs board-image; \
	fi

# Explicit full repacking resets persistent volumes only after successful packing.
board-rebuild: image board-initramfs board-guest-images
	@echo "Rebuilding $(BOARD_IMAGE): disk data will be reset after successful packing."
	$(MAKE) -o image -o board-initramfs -o board-guest-images board-image BOARD_IMAGE_REPLACE=--replace

board-guest-images: guest-itb
	python3 -B scripts/pack-guest-disk.py --board "$(BOARD_CONFIG)" \
		--rootfs "$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/rootfs.tar" --output "$(BOARD_OUTPUT)/alpine.ext4"
	"$(FIT_PACK)" "$(BOARD_OUTPUT)/alpine.itb" "$(NATIVE_GUEST_ARCH)" "$(NATIVE_GUEST_MEMORY_BYTES)" "$(NATIVE_GUEST_VCPUS)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/Image" "$(NATIVE_GUEST_LOAD)" "$(NATIVE_GUEST_LOAD)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/initramfs.cpio.gz" \
		"$(NATIVE_GUEST_BOOTARGS) hyper.root=/dev/sda"

board-image: image board-initramfs board-guest-images
	python3 -B scripts/pack-board-image.py --board "$(BOARD_CONFIG)" --output "$(BOARD_IMAGE)" $(BOARD_IMAGE_REPLACE) \
		--default-artifact "hyper=$(KERNEL_IMAGE)" --default-artifact "bootstrap=$(BOARD_OUTPUT)/bootstrap.cpio" \
		--default-artifact "alpine=$(BOARD_OUTPUT)/alpine.itb" \
		--default-artifact "alpine-rootfs=$(BOARD_OUTPUT)/alpine.ext4" $(BOARD_ARTIFACTS)

board-run:
	@test "$(BOARD)" = qemu || { echo "board-run requires the QEMU deployment profile" >&2; exit 2; }
	$(NATIVE_QEMU_ENV) python3 -B scripts/run-io-vm.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$(BOARD_OUTPUT)/bootstrap.cpio" \
		--disk "$(BOARD_IMAGE)" --board "$(BOARD_CONFIG)"

.PHONY: test-board-storage
test-board-storage: app image
	@test "$(ARCH)" = aarch64 || { echo "board storage acceptance requires aarch64" >&2; exit 2; }
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" HYPER_RUST_STD=1 \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml \
		--release -p hyper-io-runtime --features storage-probe --bin hyper-storage-probe
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/run.XXXXXX") && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		BOARD_CONFIG="$(CURDIR)/boards/qemu.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" \
		BOARD_EXTRA_ENTRIES='0755 bin/storage-probe "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-storage-probe"' && \
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-board-storage.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$$fixture/bootstrap.cpio" \
		--disk "$$fixture/disk.img" --board "$(CURDIR)/boards/qemu.json" --log "$$fixture/accept" \
		$(if $(filter 1,$(STACK_AUDIT)),--minimum-stack-remaining "$(STACK_MINIMUM_REMAINING)" --maximum-stack-used "$(STACK_MAXIMUM_USED)")

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

# Fault injection is compiled into a separate output tree; normal app artifacts
# and subsequent make run images never inherit the test features.
.PHONY: test-board-broker
test-board-broker: app image fit-pack
	@test "$(ARCH)" = aarch64 || { echo "broker acceptance requires aarch64" >&2; exit 2; }
	CARGO_TARGET_DIR="$(CURDIR)/target/app-broker-tests/$(NATIVE_ARCH)" \
		HYPER_RUST_STD=1 HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml --release --locked \
		-p hyper-io-runtime -p hyper-vm-manager -p hyper-vm-runtime \
		--features hyper-io-runtime/broker-test,hyper-vm-manager/broker-test,hyper-vm-runtime/broker-test
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
		APP_CARGO_OUTPUT="$(CURDIR)/target/app-broker-tests/$(NATIVE_ARCH)" \
		NATIVE_VM_MANAGER="$(CURDIR)/target/app-broker-tests/$(NATIVE_ARCH)/$(NATIVE_RUST_TARGET)/release/hyper-vm-manager" \
		NATIVE_VM_RUNTIME="$(CURDIR)/target/app-broker-tests/$(NATIVE_ARCH)/$(NATIVE_RUST_TARGET)/release/hyper-vm-runtime" \
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
	CARGO_TARGET_DIR="$(CURDIR)/target/app-device-tests/$(NATIVE_ARCH)" \
		HYPER_RUST_STD=1 HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml --release --locked \
		-p hyper-io-runtime --features storage-probe,userspace-device-test
	mkdir -p "$(BOARD_TEST_OUTPUT)"
	@fixture=$$(mktemp -d "$(BOARD_TEST_OUTPUT)/userspace-device.XXXXXX") && \
	python3 -B tests/qemu/prepare-userspace-device.py --qemu "$(QEMU)" \
		--board "$(CURDIR)/boards/qemu.json" --output "$$fixture" && \
	$(MAKE) -o image -o app board-image BOARD=qemu \
		APP_CARGO_OUTPUT="$(CURDIR)/target/app-device-tests/$(NATIVE_ARCH)" \
		BOARD_CONFIG="$$fixture/config.json" BOARD_OUTPUT="$$fixture" \
		BOARD_IMAGE="$$fixture/disk.img" \
		BOARD_EXTRA_ENTRIES='0755 bin/storage-probe "$(CURDIR)/target/app-device-tests/$(NATIVE_ARCH)/$(NATIVE_RUST_TARGET)/release/hyper-storage-probe"' && \
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
