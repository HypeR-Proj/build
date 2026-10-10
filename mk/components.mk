# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Read-only component registry. No compilation, SDK discovery or file writes.
# Invoke from the repository root; component templates only register metadata.
include mk/components/rust.mk
include mk/components/files.mk
include $(sort $(wildcard app/*/component.mk lib/*/component.mk))
include tests/native/component.mk

$(eval $(call native-symlink,lib64,lib,system development))

.PHONY: component-records
component-records:
	@printf '%s\n' $(NATIVE_COMPONENT_RECORDS)
