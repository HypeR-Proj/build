# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# The repository root composes independently owned Kernel, SDK, and application
# domains. Kernel-only mechanics live in kernel/Makefile.
ARCH ?= aarch64
CARGO ?= cargo
NATIVE_ARCH := aarch64
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
NATIVE_ECHO := $(APP_OUTPUT)/echo
NATIVE_STATIC_ECHO := $(APP_OUTPUT)/echo-static
NATIVE_PS := $(APP_OUTPUT)/ps
NATIVE_HANDLE := $(APP_OUTPUT)/handle
NATIVE_LS := $(APP_OUTPUT)/ls
NATIVE_FREE := $(APP_OUTPUT)/free
NATIVE_TOP := $(APP_OUTPUT)/top
NATIVE_DYNAMIC_TEST := $(APP_OUTPUT)/dynamic-test
NATIVE_DYNAMIC_PLUGIN := $(APP_OUTPUT)/libdynamic-probe.so
NATIVE_SERVICE_MANIFEST := $(CURDIR)/app/config/services.json
NATIVE_INITRAMFS := $(APP_OUTPUT)/initramfs.cpio
NATIVE_LOADER := $(SDK_OUTPUT)/lib/ld-hyper-aarch64.so
NATIVE_RUNTIME_LIBRARY := $(SDK_OUTPUT)/lib/libhyper.so
NEWC_PACK := $(CURDIR)/target/host-tools/newc-pack

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
LLVM_RANLIB ?= $(shell sh scripts/find-llvm-tool.sh llvm-ranlib)
HYPER_LD ?= $(shell sh scripts/find-llvm-tool.sh ld.lld)

QEMU ?= qemu-system-aarch64
QEMU_CPU ?= cortex-a72
QEMU_CPUS ?= 4
QEMU_MEMORY ?= 512M
QEMU_BOOTARGS ?= earlycon=pl011,mmio32,0x09000000

NATIVE_RUN_PREREQUISITES :=
ifeq ($(ARCH),aarch64)
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

.PHONY: all $(KERNEL_TARGETS) sdk sdk-check sdk-test app app-check app-test \
	native-initramfs test-native check-all test-all verify-all run clean

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
		$(MAKE) -C "$(SDK_TOOLCHAIN_SOURCE)" sysroot \
		ABI_SOURCE="$(SDK_ABI_SOURCE)" \
		LIB_SOURCE="$(SDK_LIB_SOURCE)" \
		LOADER_SOURCE="$(SDK_LOADER_SOURCE)" \
		RUST_SOURCE="$(SDK_RUST_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)"

sdk-check:
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
		--workspace --target aarch64-unknown-none --lib -- -D warnings
	CARGO_TARGET_DIR="$(CURDIR)/target/sdk-rust-host" $(CARGO) clippy \
		--manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" -p hyper-os -p hyper-service -p hyper-sys \
		--all-targets -- -D warnings
	HYPER_SDK_VERSION="$(SDK_VERSION)" \
		HYPER_SDK_SOURCE_REVISION="$(SDK_SOURCE_REVISION)" \
		CLANG="$(CLANG)" HOST_CC="$(HOST_CC)" \
		LLVM_AR="$(LLVM_AR)" LLVM_RANLIB="$(LLVM_RANLIB)" \
		HYPER_LD="$(HYPER_LD)" \
		$(MAKE) -C "$(SDK_TOOLCHAIN_SOURCE)" check \
		ABI_SOURCE="$(SDK_ABI_SOURCE)" \
		LIB_SOURCE="$(SDK_LIB_SOURCE)" \
		LOADER_SOURCE="$(SDK_LOADER_SOURCE)" \
		RUST_SOURCE="$(SDK_RUST_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)" \
		TEST_OUTPUT="$(CURDIR)/target/sdk-check" \
		SMOKE_SOURCE="$(SDK_LIB_SOURCE)/test-app/main.c"

sdk-test:
	CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) test \
		--manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-features
	CARGO_TARGET_DIR="$(CURDIR)/target/sdk-rust-tests" $(CARGO) test \
		--manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" -p hyper-os -p hyper-sys
	cmake -S "$(SDK_LIB_SOURCE)/tests/unit" -B "$(SDK_LIB_TEST_OUTPUT)" \
		-DCMAKE_C_COMPILER="$(HOST_CC)" \
		-DHYPER_ABI_INCLUDE_DIR="$(SDK_ABI_SOURCE)/include"
	cmake --build "$(SDK_LIB_TEST_OUTPUT)"
	ctest --test-dir "$(SDK_LIB_TEST_OUTPUT)" --output-on-failure

app: sdk
	mkdir -p "$(APP_OUTPUT)"
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "app/Cargo.toml" --workspace --release --locked --offline
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-init" \
		"$(NATIVE_INIT)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-session-service" \
		"$(NATIVE_SESSION_SERVICE)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-console-input" \
		"$(NATIVE_CONSOLE_INPUT)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-console-output" \
		"$(NATIVE_CONSOLE_OUTPUT)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-shell" \
		"$(NATIVE_SHELL)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-echo" \
		"$(NATIVE_ECHO)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-ps" \
		"$(NATIVE_PS)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-handle" \
		"$(NATIVE_HANDLE)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-ls" \
		"$(NATIVE_LS)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-free" \
		"$(NATIVE_FREE)"
	install -m 0755 \
		"$(APP_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-top" \
		"$(NATIVE_TOP)"
	CARGO_TARGET_DIR="$(APP_STATIC_CARGO_OUTPUT)" HYPER_LINK_MODE=static \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "app/Cargo.toml" --bin hyper-echo --release --locked --offline
	install -m 0755 \
		"$(APP_STATIC_CARGO_OUTPUT)/aarch64-unknown-none/release/hyper-echo" \
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

app-check: sdk
	$(CARGO) fmt --manifest-path "app/Cargo.toml" --all -- --check
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" clippy \
		--manifest-path "app/Cargo.toml" --workspace --locked --offline -- -D warnings

app-test: sdk
	CARGO_TARGET_DIR="$(CURDIR)/target/app-host-tests" $(CARGO) test \
		--manifest-path "app/Cargo.toml" --lib \
		--target "$(HOST_TARGET)" --locked --offline \
		--config "patch.crates-io.hyper-abi.path = '$(SDK_OUTPUT)/share/hyper/abi'" \
		--config "patch.crates-io.hyper-os.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-os'" \
		--config "patch.crates-io.hyper-rt.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-rt'" \
		--config "patch.crates-io.hyper-service.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-service'" \
		--config "patch.crates-io.hyper-sys.path = '$(SDK_OUTPUT)/share/hyper/rust/hyper-sys'"

$(NEWC_PACK): tools/newc-pack.c
	mkdir -p "$(dir $(NEWC_PACK))"
	"$(HOST_CC)" -std=c17 -Wall -Wextra -Werror "$<" -o "$@"

native-initramfs: app $(NEWC_PACK)
	"$(NEWC_PACK)" \
		0755 init "$(NATIVE_INIT)" \
		0755 svc/console-input "$(NATIVE_CONSOLE_INPUT)" \
		0755 svc/console-output "$(NATIVE_CONSOLE_OUTPUT)" \
		0755 svc/session "$(NATIVE_SESSION_SERVICE)" \
		0755 bin/sh "$(NATIVE_SHELL)" \
		0755 bin/echo "$(NATIVE_ECHO)" \
		0755 bin/echo-static "$(NATIVE_STATIC_ECHO)" \
		0755 bin/ps "$(NATIVE_PS)" \
		0755 bin/handle "$(NATIVE_HANDLE)" \
		0755 bin/ls "$(NATIVE_LS)" \
		0755 bin/free "$(NATIVE_FREE)" \
		0755 bin/top "$(NATIVE_TOP)" \
		0755 bin/dynamic-test "$(NATIVE_DYNAMIC_TEST)" \
		0755 lib/ld-hyper-aarch64.so "$(NATIVE_LOADER)" \
		0755 lib/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)" \
		0755 lib/libdynamic-probe.so "$(NATIVE_DYNAMIC_PLUGIN)" \
		0644 etc/hyper/services.json "$(NATIVE_SERVICE_MANIFEST)" \
		> "$(NATIVE_INITRAMFS).first"
	"$(NEWC_PACK)" \
		0755 init "$(NATIVE_INIT)" \
		0755 svc/console-input "$(NATIVE_CONSOLE_INPUT)" \
		0755 svc/console-output "$(NATIVE_CONSOLE_OUTPUT)" \
		0755 svc/session "$(NATIVE_SESSION_SERVICE)" \
		0755 bin/sh "$(NATIVE_SHELL)" \
		0755 bin/echo "$(NATIVE_ECHO)" \
		0755 bin/echo-static "$(NATIVE_STATIC_ECHO)" \
		0755 bin/ps "$(NATIVE_PS)" \
		0755 bin/handle "$(NATIVE_HANDLE)" \
		0755 bin/ls "$(NATIVE_LS)" \
		0755 bin/free "$(NATIVE_FREE)" \
		0755 bin/top "$(NATIVE_TOP)" \
		0755 bin/dynamic-test "$(NATIVE_DYNAMIC_TEST)" \
		0755 lib/ld-hyper-aarch64.so "$(NATIVE_LOADER)" \
		0755 lib/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)" \
		0755 lib/libdynamic-probe.so "$(NATIVE_DYNAMIC_PLUGIN)" \
		0644 etc/hyper/services.json "$(NATIVE_SERVICE_MANIFEST)" \
		> "$(NATIVE_INITRAMFS).second"
	cmp "$(NATIVE_INITRAMFS).first" "$(NATIVE_INITRAMFS).second"
	mv "$(NATIVE_INITRAMFS).first" "$(NATIVE_INITRAMFS)"
	rm -f "$(NATIVE_INITRAMFS).second"

test-native: image native-initramfs
	sh tests/qemu/verify-native-init.sh \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(NATIVE_INITRAMFS)" \
		"$(QEMU_CPU)" "$(QEMU_CPUS)" "$(QEMU_MEMORY)" "$(QEMU_BOOTARGS)"

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
