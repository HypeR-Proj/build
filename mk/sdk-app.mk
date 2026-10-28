# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# SDK assembly and application build/check contracts.
# Native-only Rust DSO targets export SDK calls that have no host implementation.
# Test their implementation crates on the host; exercise delivery through QEMU.
APP_HOST_EXCLUDES = $(shell python3 -B scripts/app-deployment.py host-excludes --manifest "$(APP_DEPLOYMENT)")

# Bootstrap programs are built independently: a workspace-wide Cargo build can
# unify shared-library features back into these otherwise static consumers.
ifeq ($(origin APP_BOOT_COMPONENTS),undefined)
APP_BOOT_COMPONENTS := $(shell python3 -B scripts/app-deployment.py boot-components --manifest "$(APP_DEPLOYMENT)" $(SYSTEM_CONFIG_ARGS))
endif
APP_BOOT_OUTPUT ?= $(APP_OUTPUT)/boot-static
APP_BOOT_CARGO_OUTPUT ?= $(APP_CARGO_OUTPUT)/boot-static
APP_BOOT_SELECTION = $(addprefix --component ,$(APP_BOOT_COMPONENTS))

.PHONY: sdk sdk-check sdk-test app app-fetch app-check app-test app-manifest
app-manifest:
	python3 -B scripts/app-deployment.py manifest --manifest "$(APP_DEPLOYMENT)" --profile "$(APP_BUILD_PROFILE)" $(SYSTEM_CONFIG_ARGS)

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
		DYNAMIC_LOADER_SOURCE="$(SDK_DYNAMIC_LOADER_SOURCE)" \
		USERSPACE_LOADER_SOURCE="$(SDK_USERSPACE_LOADER_SOURCE)" \
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
		--target "$(HOST_TARGET)" -p hyper-os -p hyper-sys -p hyper-filesystem --features hyper-os/std \
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
		DYNAMIC_LOADER_SOURCE="$(SDK_DYNAMIC_LOADER_SOURCE)" \
		USERSPACE_LOADER_SOURCE="$(SDK_USERSPACE_LOADER_SOURCE)" \
		RUST_SOURCE="$(SDK_RUST_SOURCE)" \
		OUTPUT="$(SDK_OUTPUT)" \
		TEST_OUTPUT="$(CURDIR)/target/sdk-check/$(NATIVE_ARCH)" \
		SMOKE_SOURCE="$(SDK_LIB_SOURCE)/test-app/main.c"

sdk-test:
	HOST_CC="$(HOST_CC)" CARGO_TARGET_DIR="$(SDK_ABI_TARGET)" $(CARGO) test \
		--manifest-path "$(SDK_ABI_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" --all-features
	CARGO_TARGET_DIR="$(CURDIR)/target/sdk-rust-tests" $(CARGO) test \
		--manifest-path "$(SDK_RUST_SOURCE)/Cargo.toml" \
		--target "$(HOST_TARGET)" -p hyper-os -p hyper-sys -p hyper-filesystem --features hyper-os/std
	CARGO_TARGET_DIR="$(FIT_PACK_TARGET)" $(CARGO) test \
		--manifest-path "tools/fit-pack/Cargo.toml" --target "$(HOST_TARGET)"
	cmake -S "$(SDK_LIB_SOURCE)/tests/unit" -B "$(SDK_LIB_TEST_OUTPUT)" \
		-DCMAKE_C_COMPILER="$(HOST_CC)" \
		-DHYPER_ABI_INCLUDE_DIR="$(SDK_ABI_SOURCE)/include"
	cmake --build "$(SDK_LIB_TEST_OUTPUT)"
	ctest --test-dir "$(SDK_LIB_TEST_OUTPUT)" --output-on-failure

app-fetch: sdk
	python3 -B tests/ci/check-workspace.py
	@set -e; for workspace in app bootstrap/stage1; do \
	HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		"$(SDK_OUTPUT)/bin/hyper-cargo" fetch --manifest-path "$$workspace/Cargo.toml" --locked; \
	done

app: app-fetch
	mkdir -p "$(APP_OUTPUT)" "$(APP_CARGO_OUTPUT)"
	@set -e; for link_mode in static dynamic; do for rust_runtime in core std; do \
	workspace=app; build_directory="$(APP_CARGO_OUTPUT)"; \
	if test "$$link_mode" = static; then workspace=bootstrap/stage1; build_directory="$(APP_BOOT_CARGO_OUTPUT)"; fi; \
	rust_std=1; if test "$$rust_runtime" = core; then rust_std=0; fi; \
	record="$(APP_CARGO_OUTPUT)/artifacts-$$link_mode-$$rust_runtime.jsonl"; : > "$$record"; \
	app_targets=$$(python3 -B scripts/app-deployment.py cargo-targets \
		--manifest "$(APP_DEPLOYMENT)" --profile "$(APP_BUILD_PROFILE)" \
		$(SYSTEM_CONFIG_ARGS) --executable-roots \
		--link-mode "$$link_mode" --rust-runtime "$$rust_runtime" \
		--features "$(APP_FEATURES)" \
		$(addprefix --extra-bin ,$(APP_EXTRA_BINS))) || exit $$?; \
	test -n "$$app_targets" || continue; \
	CARGO_TARGET_DIR="$$build_directory" HYPER_LINK_MODE="$$link_mode" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD="$$rust_std" "$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "$$workspace/Cargo.toml" --release --locked --offline \
		--message-format=json-render-diagnostics $$app_targets > "$$record"; \
	done; done
	python3 -B scripts/app-deployment.py install --manifest "$(APP_DEPLOYMENT)" \
		$(SYSTEM_CONFIG_ARGS) \
		--profile "$(APP_BUILD_PROFILE)" \
		$(foreach mode,static dynamic,$(foreach runtime,core std,--cargo-artifacts "$(APP_CARGO_OUTPUT)/artifacts-$(mode)-$(runtime).jsonl")) \
		--build "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release" --output "$(APP_OUTPUT)" \
		--static-build "$(APP_BOOT_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release" \
		--core-build "$(APP_CARGO_OUTPUT)/$(NATIVE_FREESTANDING_TARGET)/release" \
		--static-core-build "$(APP_BOOT_CARGO_OUTPUT)/$(NATIVE_FREESTANDING_TARGET)/release" \
		--sdk "$(SDK_OUTPUT)" --features "$(APP_FEATURES)" \
		$(addprefix --extra-bin ,$(APP_EXTRA_BINS))

.PHONY: app-boot
app-boot: app-fetch
	@test -n "$(strip $(APP_BOOT_COMPONENTS))" || { echo "APP_BOOT_COMPONENTS must not be empty" >&2; exit 2; }
	@set -e; for rust_runtime in core std; do \
	rust_std=1; if test "$$rust_runtime" = core; then rust_std=0; fi; \
	boot_targets=$$(python3 -B scripts/app-deployment.py cargo-targets \
		--manifest "$(APP_DEPLOYMENT)" --link-mode static $(APP_BOOT_SELECTION) \
		$(SYSTEM_CONFIG_ARGS) \
		--rust-runtime "$$rust_runtime" --features "$(APP_FEATURES)") || exit $$?; \
	test -n "$$boot_targets" || continue; \
	CARGO_TARGET_DIR="$(APP_BOOT_CARGO_OUTPUT)" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD="$$rust_std" HYPER_LINK_MODE=static "$(SDK_OUTPUT)/bin/hyper-cargo" build \
		--manifest-path "bootstrap/stage1/Cargo.toml" --release --locked --offline \
		--no-default-features $$boot_targets; \
	done
	python3 -B scripts/app-deployment.py install --manifest "$(APP_DEPLOYMENT)" \
		$(SYSTEM_CONFIG_ARGS) \
		--build "$(APP_BOOT_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release" \
		--static-core-build "$(APP_BOOT_CARGO_OUTPUT)/$(NATIVE_FREESTANDING_TARGET)/release" \
		--output "$(APP_BOOT_OUTPUT)" --sdk "$(SDK_OUTPUT)" --link-mode static \
		--features "$(APP_FEATURES)" $(APP_BOOT_SELECTION)

.PHONY: app-fixtures app-sdk-test
app-fixtures: APP_BUILD_PROFILE = development
app-fixtures: app
	HYPER_LINK_MODE=static HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		"$(SDK_OUTPUT)/bin/hyper-clang" -std=c17 -Wall -Wextra -Werror \
		-Wl,-z,stack-size=65536 tests/native/static-echo.c -o "$(NATIVE_STATIC_ECHO)"
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
	$(CARGO) fmt --manifest-path "bootstrap/stage1/Cargo.toml" --all -- --check
	@set -e; for link_mode in static dynamic; do for rust_runtime in core std; do \
	workspace=app; if test "$$link_mode" = static; then workspace=bootstrap/stage1; fi; \
	rust_std=1; if test "$$rust_runtime" = core; then rust_std=0; fi; \
	app_targets=$$(python3 -B scripts/app-deployment.py cargo-targets \
		--manifest "$(APP_DEPLOYMENT)" $(SYSTEM_CONFIG_ARGS) --executable-roots \
		--link-mode "$$link_mode" --rust-runtime "$$rust_runtime") || exit $$?; \
	test -n "$$app_targets" || continue; \
	CARGO_TARGET_DIR="$(APP_CARGO_OUTPUT)" HYPER_LINK_MODE="$$link_mode" \
		HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		HYPER_RUST_STD="$$rust_std" "$(SDK_OUTPUT)/bin/hyper-cargo" clippy \
		--manifest-path "$$workspace/Cargo.toml" --locked --offline $$app_targets -- -D warnings; \
	done; done

# APP_TEST_WORKSPACE selects a domain for a targeted APP_TEST_PACKAGE run.
APP_TEST_WORKSPACE ?= app
NATIVE_HOST_WORKSPACES = $(if $(APP_TEST_PACKAGE),$(APP_TEST_WORKSPACE),app bootstrap/stage1)
define native-host-tests
	@set -e; for workspace in $(NATIVE_HOST_WORKSPACES); do \
	excludes=; if test "$$workspace" = app; then excludes='$(APP_HOST_EXCLUDES)'; fi; \
	CARGO_TARGET_DIR="$(CURDIR)/target/app-host-tests" $(CARGO) test \
		--manifest-path "$$workspace/Cargo.toml" $(if $(APP_TEST_PACKAGE),-p "$(APP_TEST_PACKAGE)",--workspace $$excludes) \
		--target "$(HOST_TARGET)" --locked $(3) $(APP_TEST_ARGS) \
		--config "patch.crates-io.hyper-abi.path = '$(1)'" \
		--config "patch.crates-io.hyper-os.path = '$(2)/hyper-os'" \
		--config "patch.crates-io.hyper-filesystem.path = '$(2)/hyper-filesystem'" \
		--config "patch.crates-io.hyper-rt.path = '$(2)/hyper-rt'" \
		--config "patch.crates-io.hyper-sys.path = '$(2)/hyper-sys'"; \
	done
endef

app-test:
	$(call native-host-tests,$(SDK_ABI_SOURCE),$(SDK_RUST_SOURCE))

app-sdk-test: app-fetch
	$(call native-host-tests,$(SDK_OUTPUT)/share/hyper/abi,$(SDK_OUTPUT)/share/hyper/rust,--offline)
