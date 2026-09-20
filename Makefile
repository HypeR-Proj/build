# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# The repository root composes independently owned Kernel, SDK, and application
# domains. Kernel-only mechanics live in kernel/Makefile.
ARCH ?= aarch64
CARGO ?= cargo
NATIVE_ARCH := $(ARCH)
NATIVE_RUST_TARGET := $(NATIVE_ARCH)-unknown-hyper
NATIVE_FREESTANDING_TARGET_aarch64 := aarch64-unknown-none
NATIVE_FREESTANDING_TARGET_riscv64 := riscv64gc-unknown-none-elf
NATIVE_FREESTANDING_TARGET := $(NATIVE_FREESTANDING_TARGET_$(NATIVE_ARCH))
KERNEL_DIRECTORY := $(CURDIR)/kernel
KERNEL_PROFILE := kernel
KERNEL_TARGET_aarch64 := aarch64-unknown-none
KERNEL_TARGET_riscv64 := riscv64imac-unknown-none-elf
KERNEL_TARGET_x86_64 := x86_64-unknown-none
KERNEL_TARGET := $(KERNEL_TARGET_$(ARCH))
ifeq ($(KERNEL_TARGET),)
$(error unsupported ARCH '$(ARCH)')
endif
KERNEL_OUTPUT := $(KERNEL_DIRECTORY)/target/$(KERNEL_TARGET)/$(KERNEL_PROFILE)
KERNEL_IMAGE := $(KERNEL_OUTPUT)/hyper.img

SDK_ABI_SOURCE := $(CURDIR)/sdk/abi
SDK_LIB_SOURCE := $(CURDIR)/sdk/lib
SDK_LOADER_SOURCE := $(CURDIR)/sdk/loader
SDK_RUST_SOURCE := $(CURDIR)/sdk/rust
SDK_TOOLCHAIN_SOURCE := $(CURDIR)/sdk/toolchain
SDK_OUTPUT ?= $(CURDIR)/target/sdk/$(NATIVE_ARCH)
SDK_VERSION ?= source
SDK_SOURCE_REVISION ?= $(shell git describe --always --dirty 2>/dev/null || echo unknown)
SDK_ABI_TARGET := $(CURDIR)/target/sdk-abi
SDK_LIB_TEST_OUTPUT := $(CURDIR)/target/sdk-lib-tests
APP_OUTPUT ?= $(CURDIR)/target/app/$(NATIVE_ARCH)
APP_CARGO_OUTPUT := $(CURDIR)/target/app-cargo/$(NATIVE_ARCH)
APP_DEPLOYMENT := $(CURDIR)/app/deployment.json
NATIVE_IMAGE_PROFILE ?= development
ifeq ($(filter $(NATIVE_IMAGE_PROFILE),development system),)
$(error NATIVE_IMAGE_PROFILE must be development or system)
endif
APP_STATIC_CARGO_OUTPUT := $(CURDIR)/target/app-cargo-static/$(NATIVE_ARCH)
NATIVE_INIT := $(APP_OUTPUT)/init
NATIVE_STATIC_ECHO := $(APP_OUTPUT)/echo-static
# Test fixtures may substitute the packaged program without overwriting the
# canonical application build output.
NATIVE_PS_IMAGE ?= $(APP_OUTPUT)/ps
NATIVE_VM_MANAGER := $(APP_OUTPUT)/vm-manager
NATIVE_VM_RUNTIME := $(APP_OUTPUT)/vm-runtime
NATIVE_DYNAMIC_TEST := $(APP_OUTPUT)/dynamic-test
NATIVE_DYNAMIC_PLUGIN := $(APP_OUTPUT)/libdynamic-probe.so
NATIVE_STD_TEST_OUTPUT := $(CURDIR)/target/std-check/$(NATIVE_ARCH)
NATIVE_SERVICE_MANIFEST := $(CURDIR)/app/init/tests/config/services.json
NATIVE_INITRAMFS := $(APP_OUTPUT)/initramfs.cpio
NATIVE_LOADER := $(SDK_OUTPUT)/lib/ld-hyper-$(NATIVE_ARCH).so
NATIVE_RUNTIME_LIBRARY := $(SDK_OUTPUT)/lib/libhyper.so
NEWC_PACK := $(CURDIR)/target/host-tools/newc-pack
FIT_PACK_TARGET := $(CURDIR)/target/host-tools/fit-pack
FIT_PACK := $(FIT_PACK_TARGET)/release/hyper-fit-pack
NATIVE_GUEST_ITB := $(KERNEL_DIRECTORY)/target/guest/$(ARCH)/alpine.itb
NATIVE_GUEST_VCPUS ?= 1
NATIVE_SMP_GUEST_VCPUS ?= 4
NATIVE_SMP_INITRAMFS := $(APP_OUTPUT)/initramfs-smp.cpio
STACK_OUTPUT := $(CURDIR)/target/stack-audit/$(ARCH)
STACK_AUDIT ?= 0
STACK_MINIMUM_REMAINING ?= 2048
STACK_MAXIMUM_USED ?= 24576

IO_VM_REFERENCE ?=
IO_VM_PLATFORM ?= qemu
IO_VM_TEST ?= basic
ifeq ($(origin INITRAMFS),undefined)
RUN_PROFILE ?= $(if $(filter aarch64,$(ARCH)),board,native)
else
RUN_PROFILE ?= native
endif
IO_VM_DISK ?= $(APP_OUTPUT)/io-disk.img
IO_VM_ORAS ?=
IO_VM_PACKAGE ?=
BOARD ?= qemu
BOARD_CONFIG ?= $(CURDIR)/boards/$(BOARD).json
BOARD_OUTPUT ?= $(CURDIR)/target/board/$(BOARD)
BOARD_IMAGE ?= $(BOARD_OUTPUT)/disk.img
# Additional named inputs such as --artifact tfa=/path/to/bl31.bin. These are
# deployment inputs, never compile-time board selections.
BOARD_ARTIFACTS ?=
BOARD_EXTRA_ENTRIES ?=
RPI5_BOOT_PACKAGE ?= $(abspath $(CURDIR)/../HypeR-rpi5-boot/dist)
RPI5_BRINGUP_OUTPUT ?= $(CURDIR)/target/board/rpi5-native

BOARD_TEST_OUTPUT ?= $(CURDIR)/target/board-tests

HOST_TARGET ?= $(shell rustc -vV | sed -n 's/^host: //p')
ifeq ($(shell uname -s),Darwin)
UPSTREAM_CLANG := /opt/homebrew/opt/llvm/bin/clang
ifneq ($(wildcard $(UPSTREAM_CLANG)),)
export CLANG ?= $(UPSTREAM_CLANG)
endif
HOST_CC ?= /usr/bin/clang
else
HOST_CC ?= clang
endif
CLANG ?= clang
LLVM_AR ?= $(shell sh scripts/find-llvm-tool.sh llvm-ar)
LLVM_STRIP ?= $(shell sh scripts/find-llvm-tool.sh llvm-strip)
LLVM_RANLIB ?= $(shell sh scripts/find-llvm-tool.sh llvm-ranlib)
HYPER_LD ?= $(shell sh scripts/find-llvm-tool.sh ld.lld)

ifeq ($(ARCH),riscv64)
QEMU ?= qemu-system-riscv64
QEMU_CPU ?= rv64
QEMU_MACHINE ?= virt
QEMU_BOOTARGS ?= earlycon=uart8250,mmio,0x10000000
NATIVE_TEST_VM := 1
NATIVE_GUEST_ARCH := riscv
NATIVE_GUEST_LOAD := 0x80200000
NATIVE_GUEST_BOOTARGS := console=ttyS0 earlycon=uart8250,mmio,0x10000000 rdinit=/init loglevel=7
else
QEMU ?= qemu-system-aarch64
QEMU_CPU ?= max
QEMU_MACHINE ?= virt,virtualization=on,gic-version=3,dtb-randomness=on
NATIVE_TEST_VM := 1
NATIVE_GUEST_ARCH := arm64
NATIVE_GUEST_LOAD := 0x40200000
NATIVE_GUEST_BOOTARGS := console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 rdinit=/init loglevel=7
endif
NATIVE_VM_CONFIG := $(CURDIR)/app/init/tests/config/vms.json
NATIVE_GUEST_PREREQUISITES := guest-itb
NATIVE_GUEST_ENTRY := 0644 vm/alpine.itb "$(NATIVE_GUEST_ITB)"
QEMU_CPUS ?= 4
QEMU_MEMORY ?= 512M
QEMU_BOOTARGS ?= earlycon=pl011,mmio32,0x09000000

NATIVE_RUN_PREREQUISITES :=
ifneq ($(filter $(ARCH),aarch64 riscv64),)
ifeq ($(origin INITRAMFS),undefined)
INITRAMFS := $(NATIVE_INITRAMFS)
ifeq ($(RUN_PROFILE),board)
INITRAMFS := $(BOARD_OUTPUT)/bootstrap.cpio
else ifeq ($(RUN_PROFILE),io)
INITRAMFS := $(APP_OUTPUT)/initramfs-io.cpio
NATIVE_RUN_PREREQUISITES := io-initramfs
else
NATIVE_RUN_PREREQUISITES := native-initramfs
endif
endif
else
INITRAMFS ?=
endif

KERNEL_TARGETS := prepare-config config defconfig olddefconfig guest-assets \
	clean-guest-assets build image release check test test-image test-timer \
	test-qemu test-vhe-required verify verify-runtime verify-image verify-boot verify-smp

.PHONY: all $(KERNEL_TARGETS) sdk sdk-check sdk-test app app-fetch app-check app-test \
	fit-pack guest-itb native-initramfs test-native test-apps test-console test-runtime-crash test-vm-smoke test-io-vm guest-smp-initramfs test-guest-smp check-all test-all verify-all run clean

ifeq ($(ARCH),aarch64)
all: board-rebuild
else
all: image
endif

# Keep instrumentation and the inspector-authorized workload in a dedicated
# fixture. The normal ps binary and production initramfs are unchanged.
.PHONY: stack-initramfs test-stack
stack-initramfs:
	mkdir -p "$(STACK_OUTPUT)"
	$(MAKE) native-initramfs NATIVE_INITRAMFS="$(STACK_OUTPUT)/initramfs.cpio" \
		NATIVE_PS_IMAGE="$(NATIVE_STD_TEST_OUTPUT)/std-dynamic"

test-stack: stack-initramfs
	$(MAKE) image CARGO_FEATURES="--features kernel-stack-audit"
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-stack.py \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(STACK_OUTPUT)/initramfs.cpio" \
		"$(STACK_OUTPUT)/qemu.log" --minimum-remaining "$(STACK_MINIMUM_REMAINING)" \
		--maximum-used "$(STACK_MAXIMUM_USED)"

$(KERNEL_TARGETS):
	$(MAKE) -C "$(KERNEL_DIRECTORY)" $@ $(if $(filter undefined,$(origin CONFIG_FILE)),,CONFIG_FILE="$(abspath $(CONFIG_FILE))")

sdk:
	cd "$(SDK_ABI_SOURCE)" && \
		CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) run \
		--target "$(HOST_TARGET)" --features generator --bin hyper-abi -- check
	HYPER_SDK_VERSION="$(SDK_VERSION)" \
		HYPER_SDK_SOURCE_REVISION="$(SDK_SOURCE_REVISION)" \
		CLANG="$(CLANG)" HOST_CC="$(HOST_CC)" \
		LLVM_AR="$(LLVM_AR)" LLVM_RANLIB="$(LLVM_RANLIB)" \
		HYPER_LD="$(HYPER_LD)" \
		$(MAKE) -C "$(SDK_TOOLCHAIN_SOURCE)" ARCH="$(NATIVE_ARCH)" sysroot \
		ABI_SOURCE="$(SDK_ABI_SOURCE)" \
		LIB_SOURCE="$(SDK_LIB_SOURCE)" \
		LOADER_SOURCE="$(SDK_LOADER_SOURCE)" \
		RUST_SOURCE="$(SDK_RUST_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)"

sdk-check: sdk
	$(CARGO) fmt --manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" -- --check
	cd "$(SDK_ABI_SOURCE)" && \
		CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) run \
		--target "$(HOST_TARGET)" --features generator --bin hyper-abi -- check
	CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) clippy \
		--manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-targets --all-features -- -D warnings
	$(CARGO) fmt --manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" --all -- --check
	CARGO_TARGET_DIR="$(CURDIR)/target/sdk-rust" $(CARGO) clippy \
		--manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" \
		--workspace --target "$(NATIVE_FREESTANDING_TARGET)" --lib -- -D warnings
	CARGO_TARGET_DIR="$(CURDIR)/target/sdk-rust-host" $(CARGO) clippy \
		--manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" -p hyper-os -p hyper-service -p hyper-sys -p hyper-vm-image \
		--all-targets -- -D warnings
	$(CARGO) fmt --manifest-path "tools/fit-pack/Cargo.toml" -- --check
	CARGO_TARGET_DIR="$(FIT_PACK_TARGET)" $(CARGO) clippy \
		--manifest-path "tools/fit-pack/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-targets -- -D warnings
	HYPER_SDK_VERSION="$(SDK_VERSION)" \
		HYPER_SDK_SOURCE_REVISION="$(SDK_SOURCE_REVISION)" \
		CLANG="$(CLANG)" HOST_CC="$(HOST_CC)" \
		LLVM_AR="$(LLVM_AR)" LLVM_RANLIB="$(LLVM_RANLIB)" \
		HYPER_LD="$(HYPER_LD)" \
		$(MAKE) -C "$(SDK_TOOLCHAIN_SOURCE)" ARCH="$(NATIVE_ARCH)" -o sysroot check \
		ABI_SOURCE="$(SDK_ABI_SOURCE)" \
		LIB_SOURCE="$(SDK_LIB_SOURCE)" \
		LOADER_SOURCE="$(SDK_LOADER_SOURCE)" \
		RUST_SOURCE="$(SDK_RUST_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)" \
		TEST_OUTPUT="$(CURDIR)/target/sdk-check/$(NATIVE_ARCH)" \
		SMOKE_SOURCE="$(SDK_LIB_SOURCE)/test-app/main.c"

sdk-test:
	CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) test \
		--manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-features
	CARGO_TARGET_DIR="$(CURDIR)/target/sdk-rust-tests" $(CARGO) test \
		--manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" -p hyper-os -p hyper-service -p hyper-sys -p hyper-vm-image
	CARGO_TARGET_DIR="$(FIT_PACK_TARGET)" $(CARGO) test \
		--manifest-path "tools/fit-pack/Cargo.toml" --target "$(HOST_TARGET)"
	cmake -S "$(SDK_LIB_SOURCE)/tests/unit" -B "$(SDK_LIB_TEST_OUTPUT)" \
		-DCMAKE_C_COMPILER="$(HOST_CC)" \
		-DHYPER_ABI_INCLUDE_DIR="$(SDK_ABI_SOURCE)/include"
	cmake --build "$(SDK_LIB_TEST_OUTPUT)"
	ctest --test-dir "$(SDK_LIB_TEST_OUTPUT)" --output-on-failure

app-fetch: sdk
	HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" fetch --manifest-path "app/Cargo.toml" --locked

app: app-fetch
	mkdir -p "$(APP_OUTPUT)"
	@app_bins=$$(python3 -B scripts/app-deployment.py binaries --manifest "$(APP_DEPLOYMENT)") || exit $$?; \
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD=1 "$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "app/Cargo.toml" --workspace --release --locked --offline \
		$$app_bins
	python3 -B scripts/app-deployment.py install --manifest "$(APP_DEPLOYMENT)" \
		--build "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release" --output "$(APP_OUTPUT)"

.PHONY: app-fixtures app-sdk-test
app-fixtures: app
	CARGO_TARGET_DIR="$(APP_STATIC_CARGO_OUTPUT)" HYPER_LINK_MODE=static \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD=1 "$(SDK_OUTPUT)/bin/hyper-cargo" rustc \
		--manifest-path "app/Cargo.toml" -p hyper-echo --bin hyper-echo --release --locked --offline \
		-- -C link-arg=-Wl,-z,stack-size=65536
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_STATIC_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-echo" \
		"$(NATIVE_STATIC_ECHO)"
	"$(SDK_OUTPUT)/bin/hyper-brand-elf" --check-static "$(NATIVE_STATIC_ECHO)"
	HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-clang" \
		-Wl,-z,stack-size=524288 "$(CURDIR)/tests/native/dynamic-smoke.c" -o "$(NATIVE_DYNAMIC_TEST)"
	HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-clang" -std=c17 -Wall -Wextra -Werror -shared \
		-Wl,-soname,libdynamic-probe.so \
		"$(CURDIR)/tests/native/dynamic-probe.c" \
		-o "$(NATIVE_DYNAMIC_PLUGIN)"
	HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		sh sdk/toolchain/scripts/check-rust-std.sh "$(SDK_OUTPUT)" \
		"$(NATIVE_STD_TEST_OUTPUT)" sdk/toolchain/tests/std-smoke/Cargo.toml

app-check: app-fetch
	$(CARGO) fmt --manifest-path "app/Cargo.toml" --all -- --check
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD=1 "$(SDK_OUTPUT)/bin/hyper-cargo" clippy \
		--manifest-path "app/Cargo.toml" --workspace --locked --offline -- -D warnings

app-test:
	CARGO_TARGET_DIR="$(CURDIR)/target/app-host-tests" $(CARGO) test \
		--manifest-path "app/Cargo.toml" $(if $(APP_TEST_PACKAGE),-p "$(APP_TEST_PACKAGE)",--workspace) --lib \
		--target "$(HOST_TARGET)" --locked $(APP_TEST_ARGS) \
		--config "patch.crates-io.hyper-abi.path = '$(SDK_ABI_SOURCE)'" \
		--config "patch.crates-io.hyper-os.path = '$(SDK_RUST_SOURCE)/hyper-os'" \
		--config "patch.crates-io.hyper-rt.path = '$(SDK_RUST_SOURCE)/hyper-rt'" \
		--config "patch.crates-io.hyper-service.path = '$(SDK_RUST_SOURCE)/hyper-service'" \
		--config "patch.crates-io.hyper-vm-image.path = '$(SDK_RUST_SOURCE)/hyper-vm-image'" \
		--config "patch.crates-io.hyper-sys.path = '$(SDK_RUST_SOURCE)/hyper-sys'"

app-sdk-test: app-fetch
	CARGO_TARGET_DIR="$(CURDIR)/target/app-host-tests" $(CARGO) test \
		--manifest-path "app/Cargo.toml" --workspace --lib \
		--target "$(HOST_TARGET)" --locked --offline \
		--config "patch.crates-io.hyper-abi.path = '$(SDK_OUTPUT)/share/hyper/abi'" \
		--config "patch.crates-io.hyper-os.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-os'" \
		--config "patch.crates-io.hyper-rt.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-rt'" \
		--config "patch.crates-io.hyper-service.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-service'" \
		--config "patch.crates-io.hyper-vm-image.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-vm-image'" \
		--config "patch.crates-io.hyper-sys.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-sys'"

$(NEWC_PACK): tools/newc-pack.c
	mkdir -p "$(dir $(NEWC_PACK))"
	"$(HOST_CC)" -std=c17 -Wall -Wextra -Werror "$<" -o "$@"

fit-pack:
	CARGO_TARGET_DIR="$(FIT_PACK_TARGET)" $(CARGO) build \
		--manifest-path "tools/fit-pack/Cargo.toml" --release

guest-itb: fit-pack
	@test "$(NATIVE_TEST_VM)" = 1 || { echo "guest images are not implemented for $(ARCH)" >&2; exit 2; }
	$(MAKE) -C "$(KERNEL_DIRECTORY)" guest-assets ARCH="$(ARCH)"
	"$(FIT_PACK)" "$(NATIVE_GUEST_ITB)" "$(NATIVE_GUEST_ARCH)" 134217728 "$(NATIVE_GUEST_VCPUS)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/Image" \
		"$(NATIVE_GUEST_LOAD)" "$(NATIVE_GUEST_LOAD)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/initramfs.cpio.gz" \
		"$(NATIVE_GUEST_BOOTARGS)"

# Development keeps the acceptance programs; system contains all ordinary apps.
native-initramfs: app $(if $(filter development,$(NATIVE_IMAGE_PROFILE)),app-fixtures) $(NEWC_PACK) $(NATIVE_GUEST_PREREQUISITES)
	python3 -B scripts/pack-native-initramfs.py \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--output "$(NATIVE_INITRAMFS)" \
		--deployment "$(APP_DEPLOYMENT)" --profile "$(NATIVE_IMAGE_PROFILE)" \
		--apps "$(APP_OUTPUT)" --sdk "$(SDK_OUTPUT)" \
		--std "$(NATIVE_STD_TEST_OUTPUT)" --arch "$(NATIVE_ARCH)" \
		--replace "init=$(NATIVE_INIT)" \
		--replace "bin/ps=$(NATIVE_PS_IMAGE)" \
		--replace "svc/vm-manager=$(NATIVE_VM_MANAGER)" \
		--replace "svc/vm-runtime=$(NATIVE_VM_RUNTIME)" \
		$(NATIVE_GUEST_ENTRY) \
		0644 etc/hyper/vms.json "$(NATIVE_VM_CONFIG)" \
		0644 etc/hyper/services.json "$(NATIVE_SERVICE_MANIFEST)" $(NATIVE_EXTRA_ENTRIES)

NATIVE_QEMU_ENV = QEMU_MACHINE="$(QEMU_MACHINE)" QEMU_CPU="$(QEMU_CPU)" \
	QEMU_CPUS="$(QEMU_CPUS)" QEMU_MEMORY="$(QEMU_MEMORY)" \
	QEMU_BOOTARGS="$(QEMU_BOOTARGS)" HYPER_TEST_VM="$(NATIVE_TEST_VM)"

test-native: image native-initramfs
	$(NATIVE_QEMU_ENV) sh tests/qemu/verify-native-init.sh \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(NATIVE_INITRAMFS)" \
		"$(QEMU_CPU)" "$(QEMU_CPUS)" "$(QEMU_MEMORY)" "$(QEMU_BOOTARGS)"

test-apps: image native-initramfs
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-apps.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(NATIVE_INITRAMFS)" "$(APP_OUTPUT)/apps.log"

test-console: image native-initramfs
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-console.py \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(NATIVE_INITRAMFS)" "$(APP_OUTPUT)/console.log"

# Native authority fixture boots as /init, independently of product services.
test-vm-smoke: image app-fetch $(NEWC_PACK)
	@test "$(ARCH)" = riscv64 -o "$(ARCH)" = aarch64 || { echo "VM smoke fixture requires aarch64 or riscv64" >&2; exit 2; }
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" HYPER_ARCH="$(NATIVE_ARCH)" \
		HYPER_SYSROOT="$(SDK_OUTPUT)" HYPER_RUST_STD=1 \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml \
		-p hyper-vm-smoke --release --locked --offline
	mkdir -p "$(APP_OUTPUT)"
	python3 -B scripts/pack-native-initramfs.py \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--output "$(APP_OUTPUT)/vm-smoke.cpio" \
		0755 init "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-vm-smoke" \
		0755 lib/ld-hyper-$(NATIVE_ARCH).so "$(NATIVE_LOADER)" \
		0755 lib/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)"
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-vm-smoke.py \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(APP_OUTPUT)/vm-smoke.cpio" \
		"$(APP_OUTPUT)/vm-smoke-$(QEMU_CPUS).log"

# Cross-VM fixture consumes the external appliance without patching its rootfs.
test-io-vm: image app-fetch fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "I/O VM acceptance requires aarch64" >&2; exit 2; }
	@test -n "$(IO_VM_PACKAGE)" || { echo "set IO_VM_PACKAGE to a complete external boot generation or imported OCI package directory" >&2; exit 2; }
	python3 -B tests/qemu/verify-io-vm.py prepare --package "$(IO_VM_PACKAGE)" \
		--fit-pack "$(FIT_PACK)" --output "$(APP_OUTPUT)/io-vm" --test "$(IO_VM_TEST)"
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" HYPER_ARCH="$(NATIVE_ARCH)" \
		HYPER_SYSROOT="$(SDK_OUTPUT)" HYPER_RUST_STD=1 \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml \
		-p hyper-vm-runtime --bin hyper-io-smoke --release --locked --offline
	python3 -B scripts/pack-native-initramfs.py \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--output "$(APP_OUTPUT)/io-vm.cpio" \
		0755 init "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-io-smoke" \
		0755 lib/ld-hyper-$(NATIVE_ARCH).so "$(NATIVE_LOADER)" \
		0755 lib/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)" \
		0644 vm/io.itb "$(APP_OUTPUT)/io-vm/io.itb" \
		0644 vm/business.itb "$(APP_OUTPUT)/io-vm/business.itb"
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-io-vm.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$(APP_OUTPUT)/io-vm.cpio" \
		--log "$(APP_OUTPUT)/io-vm-$(IO_VM_TEST)-$(QEMU_CPUS).log" --test "$(IO_VM_TEST)"

# Explicit fixture target; ordinary app builds never enable this feature.
test-runtime-crash: image native-initramfs
	@test "$(NATIVE_TEST_VM)" = 1 || { echo "VM runtime acceptance is not implemented for $(ARCH)" >&2; exit 2; }
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" HYPER_ARCH="$(NATIVE_ARCH)" \
		HYPER_SYSROOT="$(SDK_OUTPUT)" HYPER_RUST_STD=1 \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml \
		-p hyper-vm-runtime --features test-runtime-crash --release --locked --offline
	$(MAKE) -o app native-initramfs \
		NATIVE_VM_RUNTIME="$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-vm-runtime" \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/runtime-crash.cpio"
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-runtime-crash.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(APP_OUTPUT)/runtime-crash.cpio" "$(APP_OUTPUT)/runtime-crash.log"

# Test-only binaries: no power fault injection enters ordinary app artifacts.
.PHONY: test-power-crash power-crash-case
test-power-crash: image app
	@test "$(ARCH)" = aarch64 || { echo "power crash acceptance requires AArch64" >&2; exit 2; }
	@for state in dormant pending powered-off; do \
		$(MAKE) -o image -o app power-crash-case POWER_CRASH_STATE=$$state || exit $$?; \
	done

power-crash-case:
	@case "$(POWER_CRASH_STATE)" in dormant|pending|powered-off) ;; *) exit 2 ;; esac
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" HYPER_ARCH="$(NATIVE_ARCH)" \
		HYPER_SYSROOT="$(SDK_OUTPUT)" HYPER_RUST_STD=1 \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_TEST_POWER_CRASH="$(POWER_CRASH_STATE)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build --manifest-path app/Cargo.toml \
		-p hyper-vm-runtime --features test-power-crash --release --locked --offline
	$(MAKE) -o app native-initramfs ARCH=aarch64 \
		NATIVE_GUEST_VCPUS=4 \
		NATIVE_VM_CONFIG="$(CURDIR)/app/init/tests/config/vms-power-crash.json" \
		NATIVE_GUEST_ITB="$(KERNEL_DIRECTORY)/target/guest/aarch64/alpine-smp.itb" \
		NATIVE_VM_RUNTIME="$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-vm-runtime" \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE).cpio"
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-power-crash.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE).cpio" \
		"$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE).log" "$(POWER_CRASH_STATE)"

# Keep the SMP guest fixture separate from the default single-vCPU image.
# The same archive exercises both hardware GIC backends and host overcommit.
guest-smp-initramfs: app $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "guest SMP acceptance requires AArch64" >&2; exit 2; }
	$(MAKE) -o app native-initramfs ARCH=aarch64 \
		NATIVE_GUEST_VCPUS="$(NATIVE_SMP_GUEST_VCPUS)" \
		NATIVE_GUEST_ITB="$(KERNEL_DIRECTORY)/target/guest/aarch64/alpine-smp.itb" \
		NATIVE_INITRAMFS="$(NATIVE_SMP_INITRAMFS)"

test-guest-smp: image guest-smp-initramfs
	$(NATIVE_QEMU_ENV) GUEST_CPUS="$(NATIVE_SMP_GUEST_VCPUS)" \
		python3 tests/qemu/verify-guest-smp.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(NATIVE_SMP_INITRAMFS)" "$(APP_OUTPUT)/guest-smp.log"

check-all: check sdk-check app-check

test-all: test sdk-test app-test test-native

verify-all: check-all test-all

run: $(NATIVE_RUN_PREREQUISITES)
	@test -n "$(INITRAMFS)" || { \
		echo "INITRAMFS must name a newc archive containing an executable /init" >&2; \
		exit 2; \
	}
ifeq ($(RUN_PROFILE),board)
	$(MAKE) board-run
else ifeq ($(RUN_PROFILE),io)
	@test "$(ARCH)" = aarch64 || { echo "I/O VM run profile requires aarch64" >&2; exit 2; }
	$(MAKE) image
	$(NATIVE_QEMU_ENV) python3 -B scripts/run-io-vm.py \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" \
		--initramfs "$(abspath $(INITRAMFS))" --disk "$(IO_VM_DISK)"
else
	$(MAKE) -C "$(KERNEL_DIRECTORY)" run \
		ARCH="$(ARCH)" INITRAMFS="$(abspath $(INITRAMFS))"
endif

clean:
	$(MAKE) -C "$(KERNEL_DIRECTORY)" clean
	rm -rf "$(CURDIR)/target"

# GICv2 host and Linux guest coverage with the standard Native service profile.
.PHONY: test-native-gicv2
test-native-gicv2:
	$(MAKE) test-native ARCH=aarch64 QEMU_CPU=max \
		QEMU_MACHINE=virt,virtualization=on,gic-version=2,dtb-randomness=on \
		NATIVE_INITRAMFS="$(CURDIR)/target/app/aarch64/initramfs-gicv2.cpio"

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
	python3 -B scripts/rpi5-bringup.py --package "$(RPI5_BOOT_PACKAGE)" \
		--kernel "$(KERNEL_IMAGE)" --initramfs "$(RPI5_BRINGUP_OUTPUT)/bootstrap.cpio" \
		--output "$(RPI5_BRINGUP_OUTPUT)/disk.img" $(BOARD_IMAGE_REPLACE)

.PHONY: board-plan board-initramfs board-image board-rebuild board-run board-guest-images
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

# Explicit image creation refuses existing outputs. The default build opts
# into atomic replacement; run continues to reuse the persistent disk.
board-rebuild: image board-initramfs board-guest-images
	@echo "Rebuilding $(BOARD_IMAGE): disk data will be reset after successful packing."
	$(MAKE) -o image -o board-initramfs -o board-guest-images board-image BOARD_IMAGE_REPLACE=--replace

board-guest-images: guest-itb
	python3 -B scripts/pack-guest-disk.py --board "$(BOARD_CONFIG)" \
		--rootfs "$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/rootfs.tar" --output "$(BOARD_OUTPUT)/alpine.ext4"
	"$(FIT_PACK)" "$(BOARD_OUTPUT)/alpine.itb" "$(NATIVE_GUEST_ARCH)" 134217728 "$(NATIVE_GUEST_VCPUS)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/Image" "$(NATIVE_GUEST_LOAD)" "$(NATIVE_GUEST_LOAD)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/initramfs.cpio.gz" \
		"$(NATIVE_GUEST_BOOTARGS) hyper.root=/dev/sda"

board-image: image board-initramfs board-guest-images
	python3 -B scripts/pack-board-image.py --board "$(BOARD_CONFIG)" --output "$(BOARD_IMAGE)" $(BOARD_IMAGE_REPLACE) \
		--default-artifact "hyper=$(KERNEL_IMAGE)" --default-artifact "bootstrap=$(BOARD_OUTPUT)/bootstrap.cpio" \
		--default-artifact "alpine=$(BOARD_OUTPUT)/alpine.itb" \
		--default-artifact "alpine-rootfs=$(BOARD_OUTPUT)/alpine.ext4" $(BOARD_ARTIFACTS)

board-run: image board-initramfs
	@test "$(BOARD)" = qemu || { echo "board-run requires the QEMU deployment profile" >&2; exit 2; }
	@if test ! -e "$(BOARD_IMAGE)"; then $(MAKE) -o image -o board-initramfs board-image; fi
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
