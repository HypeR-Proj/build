# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Read-only compatibility entry for the Native delivery adapter. Repository
# component.json files own membership, installation and runtime dependencies.

.PHONY: component-records
component-records:
	@python3 -B scripts/component-records.py
