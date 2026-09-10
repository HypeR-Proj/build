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
APP_STATIC_CARGO_OUTPUT := $(CURDIR)/target/app-cargo-static/$(NATIVE_ARCH)
NATIVE_INIT := $(APP_OUTPUT)/init
NATIVE_SESSION_SERVICE := $(APP_OUTPUT)/session-service
NATIVE_CONSOLE_INPUT := $(APP_OUTPUT)/console-input
NATIVE_CONSOLE_OUTPUT := $(APP_OUTPUT)/console-output
NATIVE_SHELL := $(APP_OUTPUT)/sh
NATIVE_CAT := $(APP_OUTPUT)/cat
NATIVE_MV := $(APP_OUTPUT)/mv
NATIVE_LN := $(APP_OUTPUT)/ln
NATIVE_RM := $(APP_OUTPUT)/rm
NATIVE_CHMOD := $(APP_OUTPUT)/chmod
NATIVE_CP := $(APP_OUTPUT)/cp
NATIVE_MKDIR := $(APP_OUTPUT)/mkdir
NATIVE_RMDIR := $(APP_OUTPUT)/rmdir
NATIVE_TOUCH := $(APP_OUTPUT)/touch
NATIVE_ECHO := $(APP_OUTPUT)/echo
NATIVE_STATIC_ECHO := $(APP_OUTPUT)/echo-static
NATIVE_PS := $(APP_OUTPUT)/ps
NATIVE_HANDLE := $(APP_OUTPUT)/handle
NATIVE_LS := $(APP_OUTPUT)/ls
NATIVE_FREE := $(APP_OUTPUT)/free
NATIVE_TOP := $(APP_OUTPUT)/top
NATIVE_VM_MANAGER := $(APP_OUTPUT)/vm-manager
NATIVE_VM_RUNTIME := $(APP_OUTPUT)/vm-runtime
NATIVE_VMM := $(APP_OUTPUT)/vmm
NATIVE_DYNAMIC_TEST := $(APP_OUTPUT)/dynamic-test
NATIVE_DYNAMIC_PLUGIN := $(APP_OUTPUT)/libdynamic-probe.so
NATIVE_STD_TEST_OUTPUT := $(CURDIR)/target/std-check/$(NATIVE_ARCH)
NATIVE_SERVICE_MANIFEST := $(CURDIR)/app/init/config/services.json
NATIVE_INITRAMFS := $(APP_OUTPUT)/initramfs.cpio
NATIVE_LOADER := $(SDK_OUTPUT)/lib/ld-hyper-$(NATIVE_ARCH).so
NATIVE_RUNTIME_LIBRARY := $(SDK_OUTPUT)/lib/libhyper.so
NEWC_PACK := $(CURDIR)/target/host-tools/newc-pack
FIT_PACK_TARGET := $(CURDIR)/target/host-tools/fit-pack
FIT_PACK := $(FIT_PACK_TARGET)/release/hyper-fit-pack
NATIVE_GUEST_ITB := $(KERNEL_DIRECTORY)/target/guest/aarch64/alpine.itb

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
NATIVE_TEST_VM := 0
NATIVE_SERVICE_MANIFEST := $(CURDIR)/app/init/config/services-native.json
NATIVE_VM_CONFIG := $(CURDIR)/app/init/config/vms-empty.json
NATIVE_GUEST_PREREQUISITES :=
NATIVE_GUEST_ENTRY :=
else
QEMU ?= qemu-system-aarch64
QEMU_CPU ?= cortex-a72
QEMU_MACHINE ?= virt,virtualization=on,gic-version=3,dtb-randomness=on
NATIVE_TEST_VM := 1
NATIVE_VM_CONFIG := $(CURDIR)/app/init/config/vms.json
NATIVE_GUEST_PREREQUISITES := guest-itb
NATIVE_GUEST_ENTRY := 0644 vm/alpine.itb "$(NATIVE_GUEST_ITB)"
endif
QEMU_CPUS ?= 4
QEMU_MEMORY ?= 512M
QEMU_BOOTARGS ?= earlycon=pl011,mmio32,0x09000000

NATIVE_RUN_PREREQUISITES :=
ifneq ($(filter $(ARCH),aarch64 riscv64),)
ifeq ($(origin INITRAMFS),undefined)
INITRAMFS := $(NATIVE_INITRAMFS)
NATIVE_RUN_PREREQUISITES := native-initramfs
endif
else
INITRAMFS ?=
endif

KERNEL_TARGETS := prepare-config config defconfig olddefconfig guest-assets \
	clean-guest-assets build image release check test test-image test-timer \
	test-qemu verify verify-runtime verify-image verify-boot verify-smp

.PHONY: all $(KERNEL_TARGETS) sdk sdk-check sdk-test app app-fetch app-check app-test \
	fit-pack guest-itb native-initramfs test-native test-apps test-console test-runtime-crash check-all test-all verify-all run clean

all: image

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
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD=1 "$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "app/Cargo.toml" --workspace --release --locked --offline
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-init" \
		"$(NATIVE_INIT)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-session-service" \
		"$(NATIVE_SESSION_SERVICE)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-console-input" \
		"$(NATIVE_CONSOLE_INPUT)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-console-output" \
		"$(NATIVE_CONSOLE_OUTPUT)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-shell" \
		"$(NATIVE_SHELL)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-cat" \
		"$(NATIVE_CAT)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-mv" \
		"$(NATIVE_MV)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-ln" \
		"$(NATIVE_LN)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-rm" \
		"$(NATIVE_RM)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-chmod" \
		"$(NATIVE_CHMOD)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-cp" \
		"$(NATIVE_CP)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-mkdir" \
		"$(NATIVE_MKDIR)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-rmdir" \
		"$(NATIVE_RMDIR)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-touch" \
		"$(NATIVE_TOUCH)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-echo" \
		"$(NATIVE_ECHO)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-ps" \
		"$(NATIVE_PS)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-handle" \
		"$(NATIVE_HANDLE)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-ls" \
		"$(NATIVE_LS)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-free" \
		"$(NATIVE_FREE)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-top" \
		"$(NATIVE_TOP)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-vm-manager" \
		"$(NATIVE_VM_MANAGER)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-vm-runtime" \
		"$(NATIVE_VM_RUNTIME)"
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-vmm" \
		"$(NATIVE_VMM)"
	CARGO_TARGET_DIR="$(APP_STATIC_CARGO_OUTPUT)" HYPER_LINK_MODE=static \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD=1 "$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "app/Cargo.toml" --bin hyper-echo --release --locked --offline
	sh scripts/install-if-changed.sh 0755 \
		"$(APP_STATIC_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/hyper-echo" \
		"$(NATIVE_STATIC_ECHO)"
	"$(SDK_OUTPUT)/bin/hyper-brand-elf" --check-static "$(NATIVE_STATIC_ECHO)"
	HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-clang" \
		"$(CURDIR)/tests/native/dynamic-smoke.c" -o "$(NATIVE_DYNAMIC_TEST)"
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

app-test: app-fetch
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
	$(MAKE) -C "$(KERNEL_DIRECTORY)" guest-assets ARCH=aarch64
	"$(FIT_PACK)" "$(NATIVE_GUEST_ITB)" arm64 134217728 1 \
		"$(KERNEL_DIRECTORY)/target/guest/aarch64/Image" \
		0x40200000 0x40200000 \
		"$(KERNEL_DIRECTORY)/target/guest/aarch64/initramfs.cpio.gz" \
		"console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 rdinit=/init loglevel=7"

native-initramfs: app $(NEWC_PACK) $(NATIVE_GUEST_PREREQUISITES)
	python3 scripts/pack-native-initramfs.py \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--output "$(NATIVE_INITRAMFS)" \
		0755 init "$(NATIVE_INIT)" \
		0755 svc/console-input "$(NATIVE_CONSOLE_INPUT)" \
		0755 svc/console-output "$(NATIVE_CONSOLE_OUTPUT)" \
		0755 svc/session "$(NATIVE_SESSION_SERVICE)" \
		0755 bin/sh "$(NATIVE_SHELL)" \
		0755 bin/cat "$(NATIVE_CAT)" \
		0755 bin/mv "$(NATIVE_MV)" \
		0755 bin/ln "$(NATIVE_LN)" \
		0755 bin/rm "$(NATIVE_RM)" \
		0755 bin/chmod "$(NATIVE_CHMOD)" \
		0755 bin/cp "$(NATIVE_CP)" \
		0755 bin/mkdir "$(NATIVE_MKDIR)" \
		0755 bin/rmdir "$(NATIVE_RMDIR)" \
		0755 bin/touch "$(NATIVE_TOUCH)" \
		0755 bin/echo "$(NATIVE_ECHO)" \
		0755 bin/echo-static "$(NATIVE_STATIC_ECHO)" \
		0755 bin/ps "$(NATIVE_PS)" \
		0755 bin/handle "$(NATIVE_HANDLE)" \
		0755 bin/ls "$(NATIVE_LS)" \
		0755 bin/free "$(NATIVE_FREE)" \
		0755 bin/top "$(NATIVE_TOP)" \
		0755 bin/vmm "$(NATIVE_VMM)" \
		0755 svc/vm-manager "$(NATIVE_VM_MANAGER)" \
		0755 svc/vm-runtime "$(NATIVE_VM_RUNTIME)" \
		$(NATIVE_GUEST_ENTRY) \
		0755 bin/dynamic-test "$(NATIVE_DYNAMIC_TEST)" \
		0755 bin/std-test "$(NATIVE_STD_TEST_OUTPUT)/std-dynamic" \
		0755 bin/std-test-static "$(NATIVE_STD_TEST_OUTPUT)/std-static" \
		0755 lib/ld-hyper-$(NATIVE_ARCH).so "$(NATIVE_LOADER)" \
		0755 lib/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)" \
		0755 lib/libdynamic-probe.so "$(NATIVE_DYNAMIC_PLUGIN)" \
		0644 etc/hyper/vms.json "$(NATIVE_VM_CONFIG)" \
		0644 etc/hyper/services.json "$(NATIVE_SERVICE_MANIFEST)"

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
	python3 tests/qemu/verify-runtime-crash.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(APP_OUTPUT)/runtime-crash.cpio" "$(APP_OUTPUT)/runtime-crash.log"

check-all: check sdk-check app-check

test-all: test sdk-test app-test test-native

verify-all: check-all test-all

run: $(NATIVE_RUN_PREREQUISITES)
	@test -n "$(INITRAMFS)" || { \
		echo "INITRAMFS must name a newc archive containing an executable /init" >&2; \
		exit 2; \
	}
	$(MAKE) -C "$(KERNEL_DIRECTORY)" run \
		ARCH="$(ARCH)" INITRAMFS="$(abspath $(INITRAMFS))"

clean:
	$(MAKE) -C "$(KERNEL_DIRECTORY)" clean
	rm -rf "$(CURDIR)/target"
