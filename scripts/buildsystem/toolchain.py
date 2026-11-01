# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Carry the Native Make compiler policy into Python build entry points."""

import os
from pathlib import Path
import subprocess
import sys


def native_environment(root, environment=None):
    result = dict(os.environ if environment is None else environment)
    upstream = Path('/opt/homebrew/opt/llvm/bin/clang')
    default = str(upstream) if sys.platform == 'darwin' and upstream.is_file() else 'clang'
    compiler = result.get('HYPER_CLANG') or result.get('CLANG') or default
    result['HYPER_CLANG'] = compiler
    # The SDK Make entry also uses CLANG for its C++ sources. Keep that exact
    # driver identity, including versioned names, so both entries share caches.
    result.setdefault('HYPER_CXX', compiler)
    for name, tool in [('HYPER_LD', 'ld.lld'), ('LLVM_AR', 'llvm-ar')]:
        if name not in result:
            result[name] = subprocess.check_output(
                ['sh', str(root / 'scripts/find-llvm-tool.sh'), tool],
                cwd=root, env=result, text=True).strip()
    return result
