# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Arguments: source (deployment root placeholders allowed), destination, mode,
# profiles, optional boot/system environment (default system). Explicit files also cover libraries opened at runtime with dlopen.
define native-file
NATIVE_COMPONENT_RECORDS += 'source|$(1)|$(2)|$(3)|$(4)|$(if $(5),$(5),system)'
endef

define native-symlink
NATIVE_COMPONENT_RECORDS += 'symlink|$(1)|$(2)|0777|$(3)|$(if $(4),$(4),system)'
endef
