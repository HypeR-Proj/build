# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Shared source-tree entry. Backend recipes run only in the prepared OUT view.
.DEFAULT_GOAL := all
PYTHON ?= python3
HYPER_SOURCE_WORKSPACE := $(shell $(PYTHON) "$(HYPER_BUILD_SYSTEM)/project.py" root)
OUT ?= $(HYPER_SOURCE_WORKSPACE)/out
HYPER_MAKE_OVERRIDES := $(foreach name,$(.VARIABLES),$(if $(filter command line,$(origin $(name))),$(name)))
.EXPORT_ALL_VARIABLES:
quote = '$(subst ','"'"',$(1))'

.PHONY: all $(MAKECMDGOALS)
$(sort all $(MAKECMDGOALS)):
	+@$(PYTHON) $(call quote,$(HYPER_BUILD_SYSTEM)/scripts/build.py) \
		--workspace $(call quote,$(HYPER_SOURCE_WORKSPACE)) --out $(call quote,$(OUT)) \
		--module-source $(call quote,$(CURDIR)) $(call quote,$@)
