# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Native image composition and acceptance fixtures.
fit-pack:
	CARGO_TARGET_DIR="$(FIT_PACK_TARGET)" $(CARGO) build \
		--manifest-path "tools/fit-pack/Cargo.toml" --release

guest-itb: fit-pack
	@test "$(NATIVE_TEST_VM)" = 1 || { echo "guest images are not implemented for $(ARCH)" >&2; exit 2; }
	$(MAKE) -C "$(KERNEL_DIRECTORY)" guest-assets ARCH="$(ARCH)"
	"$(FIT_PACK)" "$(NATIVE_GUEST_ITB)" "$(NATIVE_GUEST_ARCH)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/Image" \
		"$(NATIVE_GUEST_LOAD)" "$(NATIVE_GUEST_LOAD)" \
		"$(KERNEL_DIRECTORY)/target/guest/$(ARCH)/initramfs.cpio.gz"

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
		$(if $(strip $(NATIVE_ENTRY_MANIFEST)),--entries-from "$(NATIVE_ENTRY_MANIFEST)") \
		$(NATIVE_GUEST_ENTRY) \
		$(if $(strip $(NATIVE_VM_CONFIG)),0644 etc/hyper/vms.json "$(NATIVE_VM_CONFIG)") \
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

.PHONY: test-clock
# The no-RTC case intentionally tests Native std without starting a guest VM.
test-clock: NATIVE_SERVICE_MANIFEST = $(CURDIR)/app/init/config/services-console-only.json
test-clock: NATIVE_VM_CONFIG =
test-clock: image native-initramfs
	@test "$(ARCH)" = aarch64 || { echo "clock fixture requires aarch64" >&2; exit 2; }
	python3 tests/qemu/verify-clock.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(NATIVE_INITRAMFS)" "$(APP_OUTPUT)/clock"

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
		symlink lib lib64 \
		0755 lib64/ld-hyper-$(NATIVE_ARCH).so "$(NATIVE_LOADER)" \
		0755 lib64/userspace-loader-hyper-$(NATIVE_ARCH) "$(NATIVE_USERSPACE_LOADER)" \
		0755 $(NATIVE_LIBRARY_DIRECTORY)/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)"
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-vm-smoke.py \
		"$(QEMU)" "$(KERNEL_IMAGE)" "$(APP_OUTPUT)/vm-smoke.cpio" \
		"$(APP_OUTPUT)/vm-smoke-$(QEMU_CPUS).log"

# Cross-VM fixture consumes the external appliance without patching its rootfs.
test-io-vm: image app-fetch fit-pack $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "I/O VM acceptance requires aarch64" >&2; exit 2; }
	@package="$(IO_VM_PACKAGE)"; \
	if test -z "$$package"; then \
		package=$$(python3 -B scripts/fetch-io-vm.py --platform qemu \
			--reference "$(IO_VM_REFERENCE)" --oras "$(IO_VM_ORAS)") || exit $$?; \
	fi; \
	python3 -B tests/qemu/verify-io-vm.py prepare --package "$$package" \
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
		symlink lib lib64 \
		0755 lib64/ld-hyper-$(NATIVE_ARCH).so "$(NATIVE_LOADER)" \
		0755 lib64/userspace-loader-hyper-$(NATIVE_ARCH) "$(NATIVE_USERSPACE_LOADER)" \
		0755 $(NATIVE_LIBRARY_DIRECTORY)/libhyper.so "$(NATIVE_RUNTIME_LIBRARY)" \
		0755 $(NATIVE_LIBRARY_DIRECTORY)/libhyper_rust_std.so "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/deps/libhyper_rust_std.so" \
		0755 $(NATIVE_LIBRARY_DIRECTORY)/libhyper_vm_policy_shared.so "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/deps/libhyper_vm_policy_shared.so" \
		0755 $(NATIVE_LIBRARY_DIRECTORY)/libhyper_vm_support_shared.so "$(APP_CARGO_OUTPUT)/$(NATIVE_RUST_TARGET)/release/deps/libhyper_vm_support_shared.so" \
		0644 vm/io.itb "$(APP_OUTPUT)/io-vm/io.itb" \
		0644 etc/hyper/io-vms.json "$(APP_OUTPUT)/io-vm/io-vms.json" \
		0644 vm/business.itb "$(APP_OUTPUT)/io-vm/business.itb"
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-io-vm.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" --initramfs "$(APP_OUTPUT)/io-vm.cpio" \
		--log "$(APP_OUTPUT)/io-vm-$(IO_VM_TEST)-$(QEMU_CPUS).log" --test "$(IO_VM_TEST)"

# Explicit fixture target; ordinary app builds never enable this feature.
test-runtime-crash: image native-initramfs
	@test "$(NATIVE_TEST_VM)" = 1 || { echo "VM runtime acceptance is not implemented for $(ARCH)" >&2; exit 2; }
	python3 -B tests/qemu/vm_fixtures.py "$(NATIVE_VM_CONFIG)" "$(APP_OUTPUT)/fleet-fixtures"
	$(MAKE) -o app-fetch app APP_OUTPUT="$(APP_OUTPUT)/runtime-crash-apps" \
		APP_FEATURES=hyper-vm-runtime/test-runtime-crash
	$(MAKE) -o app native-initramfs \
		APP_OUTPUT="$(APP_OUTPUT)/runtime-crash-apps" \
		NATIVE_VM_CONFIG="$(APP_OUTPUT)/fleet-fixtures/victim-first.json" \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/runtime-crash.cpio"
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-runtime-crash.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(APP_OUTPUT)/runtime-crash.cpio" "$(APP_OUTPUT)/runtime-crash.log"
	$(MAKE) -o app native-initramfs \
		APP_OUTPUT="$(APP_OUTPUT)/runtime-crash-apps" \
		NATIVE_VM_CONFIG="$(APP_OUTPUT)/fleet-fixtures/survivor-first.json" \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/runtime-crash-reordered.cpio"
	$(NATIVE_QEMU_ENV) python3 tests/qemu/verify-runtime-crash.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(APP_OUTPUT)/runtime-crash-reordered.cpio" "$(APP_OUTPUT)/runtime-crash-reordered.log" \
		--isolation-only

.PHONY: test-storage-failure
test-storage-failure: image app $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "storage recovery acceptance requires aarch64" >&2; exit 2; }
	HYPER_ARCH="$(NATIVE_ARCH)" HYPER_SYSROOT="$(SDK_OUTPUT)" \
		HYPER_CLANG="$(CLANG)" HYPER_LD="$(HYPER_LD)" \
		python3 -B tests/qemu/verify-storage-failure.py prepare \
		--apps "$(APP_OUTPUT)" --sdk "$(SDK_OUTPUT)" \
		--packer "$(NEWC_PACK)" --strip "$(LLVM_STRIP)" \
		--output "$(APP_OUTPUT)/storage-failure"
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-storage-failure.py run \
		--qemu "$(QEMU)" --image "$(KERNEL_IMAGE)" \
		--output "$(APP_OUTPUT)/storage-failure"

# Definition admission is a bootstrap result, independent of autostart outcomes.
.PHONY: test-fleet-config
test-fleet-config: image app
	@test "$(NATIVE_TEST_VM)" = 1 || { echo "VM fleet acceptance is not implemented for $(ARCH)" >&2; exit 2; }
	python3 -B tests/qemu/vm_fixtures.py "$(NATIVE_VM_CONFIG)" "$(APP_OUTPUT)/fleet-fixtures"
	@for case in empty no-autostart malformed missing-image start-failure; do \
		$(MAKE) -o app native-initramfs \
			NATIVE_VM_CONFIG="$(APP_OUTPUT)/fleet-fixtures/$$case.json" \
			NATIVE_INITRAMFS="$(APP_OUTPUT)/fleet-$$case.cpio" || exit $$?; \
		$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-fleet-config.py \
			"$(QEMU)" "$(KERNEL_IMAGE)" "$(APP_OUTPUT)/fleet-$$case.cpio" \
			"$(APP_OUTPUT)/fleet-$$case.log" "$$case" || exit $$?; \
	done

# Test-only binaries: no power fault injection enters ordinary app artifacts.
.PHONY: test-power-crash power-crash-case
test-power-crash: image app
	@test "$(ARCH)" = aarch64 || { echo "power crash acceptance requires AArch64" >&2; exit 2; }
	@for state in dormant pending powered-off; do \
		$(MAKE) -o image -o app power-crash-case POWER_CRASH_STATE=$$state || exit $$?; \
	done

power-crash-case:
	@case "$(POWER_CRASH_STATE)" in dormant|pending|powered-off) ;; *) exit 2 ;; esac
	HYPER_TEST_POWER_CRASH="$(POWER_CRASH_STATE)" \
		$(MAKE) -o app-fetch app APP_OUTPUT="$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE)-apps" \
		APP_FEATURES=hyper-vm-runtime/test-power-crash
	$(MAKE) -o app native-initramfs ARCH=aarch64 \
		APP_OUTPUT="$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE)-apps" \
		NATIVE_VM_CONFIG="$(CURDIR)/app/init/tests/config/vms-power-crash.json" \
		NATIVE_GUEST_ITB="$(KERNEL_DIRECTORY)/target/guest/aarch64/alpine-smp.itb" \
		NATIVE_INITRAMFS="$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE).cpio"
	$(NATIVE_QEMU_ENV) python3 -B tests/qemu/verify-power-crash.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE).cpio" \
		"$(APP_OUTPUT)/power-crash-$(POWER_CRASH_STATE).log" "$(POWER_CRASH_STATE)"

# Select SMP policy from JSON; reuse the same guest payload bundle.
# The same archive exercises both hardware GIC backends and host overcommit.
guest-smp-initramfs: app $(NEWC_PACK)
	@test "$(ARCH)" = aarch64 || { echo "guest SMP acceptance requires AArch64" >&2; exit 2; }
	$(MAKE) -o app native-initramfs ARCH=aarch64 \
		NATIVE_VM_CONFIG="$(NATIVE_SMP_VM_CONFIG)" \
		NATIVE_GUEST_ITB="$(KERNEL_DIRECTORY)/target/guest/aarch64/alpine-smp.itb" \
		NATIVE_INITRAMFS="$(NATIVE_SMP_INITRAMFS)"

test-guest-smp: image guest-smp-initramfs
	$(NATIVE_QEMU_ENV) GUEST_CPUS="$$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["virtual-machines"][0]["configuration"]["vcpus"])' "$(NATIVE_SMP_VM_CONFIG)")" \
		python3 tests/qemu/verify-guest-smp.py "$(QEMU)" "$(KERNEL_IMAGE)" \
		"$(NATIVE_SMP_INITRAMFS)" "$(APP_OUTPUT)/guest-smp.log"
