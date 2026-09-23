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
# AArch64 Alpine exercises SMP by default; RISC-V guests remain single-vCPU.
NATIVE_GUEST_VCPUS ?= $(if $(filter aarch64,$(ARCH)),2,1)
NATIVE_GUEST_MEMORY_BYTES ?= 268435456
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
# Additional named inputs such as --artifact host-dtb=/path/to/bcm2712-rpi-5-b.dtb. These are
# deployment inputs, never compile-time board selections.
BOARD_ARTIFACTS ?=
BOARD_EXTRA_ENTRIES ?=
RPI5_BOOT_PACKAGE ?=
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
QEMU_MEMORY ?= 1G
QEMU_BOOTARGS ?= earlycon=pl011,mmio32,0x09000000

NATIVE_BUILD_PREREQUISITES :=
ifneq ($(filter $(ARCH),aarch64 riscv64),)
ifeq ($(origin INITRAMFS),undefined)
INITRAMFS := $(NATIVE_INITRAMFS)
ifeq ($(RUN_PROFILE),board)
INITRAMFS := $(BOARD_OUTPUT)/bootstrap.cpio
else ifeq ($(RUN_PROFILE),io)
INITRAMFS := $(APP_OUTPUT)/initramfs-io.cpio
NATIVE_BUILD_PREREQUISITES := io-initramfs
else
NATIVE_BUILD_PREREQUISITES := native-initramfs
endif
endif
else
INITRAMFS ?=
endif

KERNEL_TARGETS := prepare-config config defconfig olddefconfig guest-assets \
	clean-guest-assets build image release check test test-image test-timer \
	test-qemu test-vhe-required verify verify-runtime verify-image verify-boot verify-smp

.PHONY: all $(KERNEL_TARGETS) sdk sdk-check sdk-test app app-fetch app-check app-test \
	fit-pack guest-itb native-initramfs test-native test-apps test-console test-runtime-crash test-vm-smoke test-io-vm guest-smp-initramfs test-guest-smp check-all test-all verify-all run rebuild clean

ifeq ($(RUN_PROFILE),board)
all: board-build
rebuild: board-rebuild
else
all: image $(NATIVE_BUILD_PREREQUISITES)
rebuild: all
ifeq ($(RUN_PROFILE),io)
all:
	python3 -B -c 'import runpy, sys; from pathlib import Path; runpy.run_path("scripts/run-io-vm.py")["prepare_disk"](Path(sys.argv[1]), 64 * 1024 * 1024)' "$(IO_VM_DISK)"
endif
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

include mk/sdk-app.mk

$(NEWC_PACK): tools/newc-pack.c
	mkdir -p "$(dir $(NEWC_PACK))"
	"$(HOST_CC)" -std=c17 -Wall -Wextra -Werror "$<" -o "$@"

include mk/native-tests.mk

check-all: check sdk-check app-check

test-all: test sdk-test app-test test-native

verify-all: check-all test-all

run:
	@test -n "$(INITRAMFS)" || { \
		echo "INITRAMFS must name a newc archive containing an executable /init" >&2; \
		exit 2; \
	}
ifeq ($(RUN_PROFILE),board)
	$(MAKE) board-run
else ifeq ($(RUN_PROFILE),io)
	@test "$(ARCH)" = aarch64 || { echo "I/O VM run profile requires aarch64" >&2; exit 2; }
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

include mk/boards.mk
