# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

HYPER_BUILD_SYSTEM := $(patsubst %/,%,$(dir $(realpath $(lastword $(MAKEFILE_LIST)))))
include $(HYPER_BUILD_SYSTEM)/mk/entry.mk
