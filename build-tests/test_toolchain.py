# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Native module builds preserve tool overrides and match the Make default."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem import toolchain


class ToolchainTests(unittest.TestCase):
    def environment(self, values):
        return {'HYPER_LD': '/selected/ld.lld', 'LLVM_AR': '/selected/llvm-ar', **values}

    def test_explicit_native_tools_win_without_probing_defaults(self):
        values = self.environment({'HYPER_CLANG': '/selected/clang',
                                   'CLANG': '/other/clang', 'HYPER_CXX': '/selected/cxx'})
        with patch.object(toolchain.subprocess, 'check_output') as probe:
            result = toolchain.native_environment(Path('/unused'), values)
        self.assertEqual(result, values)
        probe.assert_not_called()

    def test_make_compiler_override_is_forwarded_to_native_drivers(self):
        values = self.environment({'CLANG': '/selected/clang'})
        result = toolchain.native_environment(Path('/unused'), values)
        self.assertEqual(result['HYPER_CLANG'], '/selected/clang')
        self.assertEqual(result['HYPER_CXX'], '/selected/clang')
        self.assertNotIn('HYPER_CLANG', values)

    def test_macos_module_uses_upstream_llvm_like_sdk_make(self):
        with patch.object(toolchain.sys, 'platform', 'darwin'), \
                patch.object(toolchain.Path, 'is_file', return_value=True):
            result = toolchain.native_environment(Path('/unused'), self.environment({}))
        self.assertEqual(result['HYPER_CLANG'], '/opt/homebrew/opt/llvm/bin/clang')
        self.assertEqual(result['HYPER_CXX'], '/opt/homebrew/opt/llvm/bin/clang')

    def test_versioned_clang_keeps_the_same_cxx_version(self):
        result = toolchain.native_environment(
            Path('/unused'), self.environment({'CLANG': '/selected/clang-21'}))
        self.assertEqual(result['HYPER_CXX'], '/selected/clang-21')

    def test_linux_respects_path_compiler(self):
        with patch.object(toolchain.sys, 'platform', 'linux'):
            result = toolchain.native_environment(Path('/unused'), self.environment({}))
        self.assertEqual(result['HYPER_CLANG'], 'clang')
        self.assertEqual(result['HYPER_CXX'], 'clang')


if __name__ == '__main__':
    unittest.main()
