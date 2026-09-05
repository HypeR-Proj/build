# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

ARCH ?= aarch64
ifeq ($(ARCH),aarch64)
TARGET := aarch64-unknown-none
QEMU ?= qemu-system-aarch64
QEMU_CPU ?= cortex-a72
QEMU_MACHINE := virt,virtualization=on,gic-version=3,dtb-randomness=on
DEFCONFIG := configs/qemu_aarch64_defconfig
QEMU_BOOTARGS ?= earlycon=pl011,mmio32,0x09000000
QEMU_TEST_SCRIPT := tests/qemu/verify-smp.sh
QEMU_TIMER_SCRIPT := tests/qemu/verify-timer.sh
else ifeq ($(ARCH),riscv64)
TARGET := riscv64imac-unknown-none-elf
QEMU ?= qemu-system-riscv64
QEMU_CPU ?= rv64
QEMU_MACHINE := virt
DEFCONFIG := configs/qemu_riscv64_defconfig
QEMU_BOOTARGS ?= earlycon=uart8250,mmio,0x10000000
QEMU_TEST_SCRIPT := tests/qemu/verify-riscv64.sh
QEMU_TIMER_SCRIPT := tests/qemu/verify-riscv64.sh
else ifeq ($(ARCH),x86_64)
TARGET := x86_64-unknown-none
QEMU ?= qemu-system-x86_64
QEMU_CPU ?= max
QEMU_MACHINE := q35,accel=tcg
QEMU_DTB_ARG = -dtb $(X86_HOST_DTB)
QEMU_RUN_PREREQUISITES = $(X86_HOST_DTB)
DEFCONFIG := configs/qemu_x86_64_defconfig
QEMU_BOOTARGS ?= earlycon=uart8250,io,0x3f8
QEMU_TEST_SCRIPT := tests/qemu/verify-x86_64.sh
QEMU_TIMER_SCRIPT := tests/qemu/verify-x86_64.sh
else
$(error unsupported ARCH '$(ARCH)')
endif
KERNEL_PROFILE := kernel
CARGO_PROFILE := --profile $(KERNEL_PROFILE)
CARGO ?= cargo
CONFIG_FILE ?= .config
CONFIG_PATH := $(abspath $(CONFIG_FILE))
KERNEL_CONFIG_ENV := HYPER_CONFIG=$(CONFIG_PATH)
CARGO_KERNEL = $(KERNEL_CONFIG_ENV) $(CARGO) build --target $(TARGET) $(CARGO_PROFILE) $(CARGO_FEATURES)
QEMU_CPUS ?= 4
QEMU_MEMORY ?= 512M
NATIVE_ARCH := aarch64
SDK_ABI_SOURCE := $(CURDIR)/sdk/abi
SDK_LIB_SOURCE := $(CURDIR)/sdk/lib
SDK_TOOLCHAIN_SOURCE := $(CURDIR)/sdk/toolchain
SDK_OUTPUT ?= $(CURDIR)/target/sdk/$(NATIVE_ARCH)
SDK_VERSION ?= source
SDK_SOURCE_REVISION ?= $(shell git describe --always --dirty 2>/dev/null || echo unknown)
SDK_ABI_TARGET := $(CURDIR)/target/sdk-abi
SDK_LIB_TEST_OUTPUT := $(CURDIR)/target/sdk-lib-tests
SYSTEM_OUTPUT ?= $(CURDIR)/target/system/$(NATIVE_ARCH)
NATIVE_INIT := $(SYSTEM_OUTPUT)/init
NATIVE_INITRAMFS := $(SYSTEM_OUTPUT)/initramfs.cpio
NEWC_PACK := $(CURDIR)/target/host-tools/newc-pack
NATIVE_RUN_PREREQUISITES :=
ifeq ($(ARCH),aarch64)
ifeq ($(origin INITRAMFS),undefined)
INITRAMFS := $(NATIVE_INITRAMFS)
NATIVE_RUN_PREREQUISITES := native-initramfs
endif
else
INITRAMFS ?=
endif
HOST_TARGET ?= $(shell rustc -vV | sed -n 's/^host: //p')
RUST_HOST := $(shell rustc -vV | sed -n 's/^host: //p')
LLVM_BIN := $(shell rustc --print sysroot)/lib/rustlib/$(RUST_HOST)/bin
OBJCOPY ?= $(LLVM_BIN)/llvm-objcopy
READOBJ ?= $(LLVM_BIN)/llvm-readobj
NM ?= $(LLVM_BIN)/llvm-nm
OBJDUMP ?= $(LLVM_BIN)/llvm-objdump
DTC ?= dtc

CARGO_FEATURES ?=

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
LLVM_RANLIB ?= $(shell sh scripts/find-llvm-tool.sh llvm-ranlib)
HYPER_LD ?= $(shell sh scripts/find-llvm-tool.sh ld.lld)

KERNEL_OUTPUT := target/$(TARGET)/$(KERNEL_PROFILE)
KERNEL_ELF := $(KERNEL_OUTPUT)/hyper
KERNEL_IMAGE := $(KERNEL_OUTPUT)/hyper.img
KERNEL_STRIPPED_ELF := $(KERNEL_OUTPUT)/hyper.stripped
KERNEL_STRIPPED_IMAGE := $(KERNEL_OUTPUT)/hyper.stripped.img
X86_SETUP_OBJECT := $(KERNEL_OUTPUT)/x86-setup.o
X86_SETUP_IMAGE := $(KERNEL_OUTPUT)/x86-setup.bin
X86_PAYLOAD_IMAGE := $(KERNEL_OUTPUT)/hyper.payload
X86_HOST_DTB := $(KERNEL_OUTPUT)/x86_64-host.dtb
KCONFIG_MANIFEST := tools/kconfig/Cargo.toml
KALLSYMS_MANIFEST := tools/kallsyms/Cargo.toml
KALLSYMS_BLOB := $(KERNEL_OUTPUT)/hyper.kallsyms
KALLSYMS_FINAL_BLOB := $(KERNEL_OUTPUT)/hyper.kallsyms.final
KALLSYMS_ELF := $(KERNEL_OUTPUT)/hyper.with-kallsyms
HOST_TEST_MANIFEST := tests/host/Cargo.toml
GUEST_OUTPUT := target/guest/$(ARCH)
ifeq ($(ARCH),aarch64)
GUEST_FETCH := tools/guest/fetch-alpine-aarch64.sh
GUEST_ASSET_STAMP := $(GUEST_OUTPUT)/.alpine-3.23.5.stamp
else ifeq ($(ARCH),riscv64)
GUEST_FETCH := tools/guest/fetch-alpine-riscv64.sh
GUEST_ASSET_STAMP := $(GUEST_OUTPUT)/.alpine-3.24.1.stamp
else
GUEST_FETCH := tools/guest/fetch-alpine-x86_64.sh
GUEST_ASSET_STAMP := $(GUEST_OUTPUT)/.alpine-3.23.5.stamp
endif
HOST_INITRD := $(GUEST_OUTPUT)/hypervisor-initrd.cpio

.PHONY: all prepare-config config defconfig olddefconfig guest-assets clean-guest-assets build image release check test test-image test-timer test-qemu verify verify-runtime verify-image verify-boot verify-smp sdk sdk-check sdk-test system native-initramfs test-native check-all test-all verify-all run clean

all: image

prepare-config:
	@if [ "$(CONFIG_FILE)" != ".config" ]; then \
		test -f "$(CONFIG_FILE)"; \
	elif ! grep -q '^CONFIG_ARCH_$(shell echo $(ARCH) | tr a-z A-Z)=y$$' .config 2>/dev/null; then \
		$(CARGO) run --quiet --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET) -- defconfig $(DEFCONFIG) "$(CONFIG_FILE)"; \
	fi

.config: Kconfig $(DEFCONFIG) $(KCONFIG_MANIFEST) tools/kconfig/src/lib.rs tools/kconfig/src/main.rs
	$(CARGO) run --quiet --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET) -- defconfig $(DEFCONFIG) .config

defconfig:
	$(CARGO) run --quiet --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET) -- defconfig $(DEFCONFIG) "$(CONFIG_FILE)"

olddefconfig:
	$(CARGO) run --quiet --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET) -- olddefconfig "$(CONFIG_FILE)" "$(CONFIG_FILE)"

config:
	$(CARGO) run --quiet --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET) -- config "$(CONFIG_FILE)" "$(CONFIG_FILE)"

$(GUEST_ASSET_STAMP): $(GUEST_FETCH) tools/guest/init tools/guest/boot.conf tools/guest/alpine-$(ARCH).manifest
	sh $(GUEST_FETCH)

guest-assets: $(GUEST_ASSET_STAMP)

$(X86_HOST_DTB): tests/qemu/x86_64-host.dts
	mkdir -p $(KERNEL_OUTPUT)
	$(DTC) -q -I dts -O dtb -o $@ $<

clean-guest-assets:
	rm -f $(GUEST_OUTPUT)/Image $(GUEST_OUTPUT)/initramfs.cpio.gz $(GUEST_OUTPUT)/alpine.cpio $(HOST_INITRD) $(GUEST_ASSET_STAMP)

build: prepare-config
	HYPER_KALLSYMS_BLOB= $(CARGO_KERNEL)

image: build
	$(CARGO) run --quiet --manifest-path $(KALLSYMS_MANIFEST) --target $(HOST_TARGET) -- $(NM) $(KERNEL_ELF) $(KALLSYMS_BLOB)
	HYPER_KALLSYMS_BLOB=$(abspath $(KALLSYMS_BLOB)) $(CARGO_KERNEL)
	$(CARGO) run --quiet --manifest-path $(KALLSYMS_MANIFEST) --target $(HOST_TARGET) -- $(NM) $(KERNEL_ELF) $(KALLSYMS_FINAL_BLOB)
	test "$$(wc -c < $(KALLSYMS_BLOB))" -eq "$$(wc -c < $(KALLSYMS_FINAL_BLOB))"
	mv $(KALLSYMS_FINAL_BLOB) $(KALLSYMS_BLOB)
	$(OBJCOPY) --update-section=.kallsyms=$(KALLSYMS_BLOB) $(KERNEL_ELF) $(KALLSYMS_ELF)
	mv $(KALLSYMS_ELF) $(KERNEL_ELF)
	$(if $(filter x86_64,$(ARCH)),$(OBJCOPY) --output-target=binary $(KERNEL_ELF) $(X86_PAYLOAD_IMAGE),$(OBJCOPY) --output-target=binary $(KERNEL_ELF) $(KERNEL_IMAGE))
	$(if $(filter x86_64,$(ARCH)),$(CLANG) -target i386-none-elf -c src/arch/x86_64/setup.S -o $(X86_SETUP_OBJECT),true)
	$(if $(filter x86_64,$(ARCH)),$(OBJCOPY) --only-section=.setup --output-target=binary $(X86_SETUP_OBJECT) $(X86_SETUP_IMAGE),true)
	$(if $(filter x86_64,$(ARCH)),cp $(X86_SETUP_IMAGE) $(KERNEL_IMAGE),true)
	$(if $(filter x86_64,$(ARCH)),dd if=$(X86_PAYLOAD_IMAGE) of=$(KERNEL_IMAGE) bs=2560 seek=1 conv=notrunc status=none,true)

# The distributable ELF is a debug-section-stripped copy of the canonical ELF.
# No separate release compilation or link exists, and allocated bytes must
# therefore produce an identical raw Image.
release: image
	$(OBJCOPY) --strip-debug $(KERNEL_ELF) $(KERNEL_STRIPPED_ELF)
	$(if $(filter x86_64,$(ARCH)),$(OBJCOPY) --output-target=binary $(KERNEL_STRIPPED_ELF) $(X86_PAYLOAD_IMAGE),$(OBJCOPY) --output-target=binary $(KERNEL_STRIPPED_ELF) $(KERNEL_STRIPPED_IMAGE))
	$(if $(filter x86_64,$(ARCH)),cp $(X86_SETUP_IMAGE) $(KERNEL_STRIPPED_IMAGE),true)
	$(if $(filter x86_64,$(ARCH)),dd if=$(X86_PAYLOAD_IMAGE) of=$(KERNEL_STRIPPED_IMAGE) bs=2560 seek=1 conv=notrunc status=none,true)
	cmp $(KERNEL_IMAGE) $(KERNEL_STRIPPED_IMAGE)

check: prepare-config
	$(KERNEL_CONFIG_ENV) $(CARGO) check --lib --bins --target $(TARGET)
	$(KERNEL_CONFIG_ENV) $(CARGO) clippy --target $(TARGET) -- -D warnings
	$(KERNEL_CONFIG_ENV) $(CARGO) clippy --target $(TARGET) --features kernel-self-test -- -D warnings
	$(KERNEL_CONFIG_ENV) $(CARGO) clippy --manifest-path $(HOST_TEST_MANIFEST) --target $(HOST_TARGET) --all-targets -- -D warnings
	$(CARGO) clippy --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET) -- -D warnings
	$(CARGO) clippy --manifest-path $(KALLSYMS_MANIFEST) --target $(HOST_TARGET) -- -D warnings

test: prepare-config
	$(KERNEL_CONFIG_ENV) $(CARGO) test --manifest-path $(HOST_TEST_MANIFEST) --target $(HOST_TARGET)
	$(CARGO) test --manifest-path $(KCONFIG_MANIFEST) --target $(HOST_TARGET)
	$(CARGO) test --manifest-path $(KALLSYMS_MANIFEST) --target $(HOST_TARGET)

verify: check test
	$(CARGO) fmt -- --check
	$(CARGO) fmt --manifest-path $(HOST_TEST_MANIFEST) -- --check
	$(CARGO) fmt --manifest-path $(KCONFIG_MANIFEST) -- --check
	$(CARGO) fmt --manifest-path $(KALLSYMS_MANIFEST) -- --check
	$(MAKE) verify-runtime ARCH=$(ARCH)
	$(MAKE) release
	$(MAKE) test-image ARCH=$(ARCH)

verify-runtime:
ifeq ($(ARCH),aarch64)
	$(MAKE) test-qemu ARCH=aarch64 QEMU_CPU=cortex-a72
	$(MAKE) test-qemu ARCH=aarch64 QEMU_CPU=max
else ifeq ($(ARCH),riscv64)
	$(MAKE) test-qemu ARCH=riscv64
else
	@echo "$(ARCH) has no runtime acceptance contract"
endif

test-image:
	sh tests/image/verify-image.sh $(ARCH) $(READOBJ) $(NM) $(OBJDUMP) $(KERNEL_ELF) $(KERNEL_IMAGE) $(KALLSYMS_BLOB)

test-timer: image guest-assets
	sh $(QEMU_TIMER_SCRIPT) $(QEMU) $(KERNEL_IMAGE) $(HOST_INITRD) $(QEMU_CPU) $(QEMU_MEMORY) "$(QEMU_BOOTARGS)"

test-timer: CARGO_FEATURES=--features kernel-self-test

test-qemu: image guest-assets
	QEMU_CPUS=$(QEMU_CPUS) sh $(QEMU_TEST_SCRIPT) $(QEMU) $(KERNEL_IMAGE) $(HOST_INITRD) $(QEMU_CPU) $(QEMU_MEMORY) "$(QEMU_BOOTARGS)"

test-qemu: CARGO_FEATURES=--features kernel-self-test

# Compatibility targets build the image before running the corresponding test.
verify-image: image
	$(MAKE) test-image

verify-boot: image
	$(MAKE) test-timer ARCH=$(ARCH) QEMU_CPU=$(QEMU_CPU)

verify-smp: image
	$(MAKE) test-qemu ARCH=$(ARCH) QEMU_CPU=$(QEMU_CPU)

sdk:
	cd "$(SDK_ABI_SOURCE)" && \
		CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) run \
		--target "$(HOST_TARGET)" --features generator --bin hyper-abi -- check
	HYPER_SDK_VERSION="$(SDK_VERSION)" \
		HYPER_SDK_SOURCE_REVISION="$(SDK_SOURCE_REVISION)" \
		CLANG="$(CLANG)" HOST_CC="$(HOST_CC)" \
		LLVM_AR="$(LLVM_AR)" LLVM_RANLIB="$(LLVM_RANLIB)" \
		$(MAKE) -C "$(SDK_TOOLCHAIN_SOURCE)" sysroot \
		ABI_SOURCE="$(SDK_ABI_SOURCE)" \
		LIB_SOURCE="$(SDK_LIB_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)"

sdk-check:
	$(CARGO) fmt --manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" -- --check
	cd "$(SDK_ABI_SOURCE)" && \
		CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) run \
		--target "$(HOST_TARGET)" --features generator --bin hyper-abi -- check
	CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) clippy \
		--manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-targets --all-features -- -D warnings
	HYPER_SDK_VERSION="$(SDK_VERSION)" \
		HYPER_SDK_SOURCE_REVISION="$(SDK_SOURCE_REVISION)" \
		CLANG="$(CLANG)" HOST_CC="$(HOST_CC)" \
		LLVM_AR="$(LLVM_AR)" LLVM_RANLIB="$(LLVM_RANLIB)" \
		HYPER_LD="$(HYPER_LD)" \
		$(MAKE) -C "$(SDK_TOOLCHAIN_SOURCE)" check \
		ABI_SOURCE="$(SDK_ABI_SOURCE)" \
		LIB_SOURCE="$(SDK_LIB_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)" \
		TEST_OUTPUT="$(CURDIR)/target/sdk-check" \
		SMOKE_SOURCE="$(SDK_LIB_SOURCE)/test-app/main.c"

sdk-test:
	CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) test \
		--manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-features
	cmake -S "$(SDK_LIB_SOURCE)/tests/unit" -B "$(SDK_LIB_TEST_OUTPUT)" \
		-DCMAKE_C_COMPILER="$(HOST_CC)" \
		-DHYPER_ABI_INCLUDE_DIR="$(SDK_ABI_SOURCE)/include"
	cmake --build "$(SDK_LIB_TEST_OUTPUT)"
	ctest --test-dir "$(SDK_LIB_TEST_OUTPUT)" --output-on-failure

system: sdk
	mkdir -p "$(SYSTEM_OUTPUT)"
	HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-clang" \
		-std=c17 -fno-builtin -fvisibility=hidden \
		-Wall -Wextra -Werror "system/apps/init/main.c" -o "$(NATIVE_INIT)"

$(NEWC_PACK): tools/newc-pack.c
	mkdir -p "$(dir $(NEWC_PACK))"
	"$(HOST_CC)" -std=c17 -Wall -Wextra -Werror "$<" -o "$@"

native-initramfs: system $(NEWC_PACK)
	"$(NEWC_PACK)" 0755 init "$(NATIVE_INIT)" > "$(NATIVE_INITRAMFS).first"
	"$(NEWC_PACK)" 0755 init "$(NATIVE_INIT)" > "$(NATIVE_INITRAMFS).second"
	cmp "$(NATIVE_INITRAMFS).first" "$(NATIVE_INITRAMFS).second"
	mv "$(NATIVE_INITRAMFS).first" "$(NATIVE_INITRAMFS)"
	rm -f "$(NATIVE_INITRAMFS).second"

test-native: image native-initramfs
	sh tests/qemu/verify-native-init.sh \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(NATIVE_INITRAMFS)" \
		"$(QEMU_CPU)" "$(QEMU_CPUS)" "$(QEMU_MEMORY)" "$(QEMU_BOOTARGS)"

check-all: check sdk-check

test-all: test sdk-test test-native

verify-all: check-all test-all

run: image $(QEMU_RUN_PREREQUISITES) $(NATIVE_RUN_PREREQUISITES)
	@test -n "$(INITRAMFS)" || { \
		echo "INITRAMFS must name a newc archive containing an executable /init" >&2; \
		exit 2; \
	}
	@test -f "$(INITRAMFS)" || { \
		echo "INITRAMFS does not exist: $(INITRAMFS)" >&2; \
		exit 2; \
	}
	$(QEMU) \
		-machine $(QEMU_MACHINE) \
		-cpu $(QEMU_CPU) \
		-smp $(QEMU_CPUS) \
		-m $(QEMU_MEMORY) \
		-nodefaults \
		-display none \
		-serial stdio \
		-monitor none \
		-no-reboot \
		-append "$(QEMU_BOOTARGS)" \
		-initrd "$(INITRAMFS)" $(QEMU_DTB_ARG) \
		-kernel $(KERNEL_IMAGE)

clean:
	$(CARGO) clean
