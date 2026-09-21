#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check composed Make policy and rust-analyzer workspace routing."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class EntrypointTests(unittest.TestCase):
    def test_make_composition_preserves_disk_policy(self):
        for arch in ('aarch64', 'riscv64', 'x86_64'):
            with tempfile.NamedTemporaryFile(mode='w', suffix='.mk') as probe:
                probe.write('__hyper_contract_dump:;\n')
                probe.flush()
                result = subprocess.run(
                    ['make', '-p', '-f', 'Makefile', '-f', probe.name, '__hyper_contract_dump',
                     f'ARCH={arch}'], cwd=ROOT, check=True, capture_output=True, text=True)
            database = result.stdout
            self.assertRegex(database, r'(?m)^\.DEFAULT_GOAL\s*:?=\s*all$')
            expected = 'board-rebuild' if arch == 'aarch64' else 'image'
            self.assertRegex(database, rf'(?m)^all: {expected}$')
            self.assertRegex(database, r'(?m)^board-run: image board-initramfs$')
            self.assertRegex(database, r'(?m)^board-rebuild: image board-initramfs board-guest-images$')
            # Ask Make for its evaluated recipes, not a guessed source filename.
            rebuild = re.search(r'(?ms)^board-rebuild:.*?(?=^[^\s#]|\Z)', database).group(0)
            self.assertIn('--replace', rebuild)
            run = re.search(r'(?ms)^board-run:.*?(?=^[^\s#]|\Z)', database).group(0)
            self.assertNotIn('--replace', run)
            self.assertIn('$(BOARD_IMAGE)', run)

    def test_analyzer_patches_only_native_workspaces(self):
        with tempfile.TemporaryDirectory(prefix='hyper editor ') as directory:
            root = Path(directory).resolve()
            (root / 'scripts').mkdir()
            script = root / 'scripts/rust-analyzer-check.sh'
            shutil.copyfile(ROOT / 'scripts/rust-analyzer-check.sh', script)
            fake = root / 'bin'
            fake.mkdir()
            recorder = ('#!' + sys.executable + '\nimport os,json,sys\n'
                        'with open(os.environ["PROBE"],"w") as f: '
                        'json.dump({"argv":sys.argv[1:],"config":os.getenv("HYPER_CONFIG"),'
                        '"std":os.getenv("HYPER_RUST_STD")},f)\n')
            (fake / 'cargo').write_text(recorder)
            (fake / 'cargo').chmod(0o755)
            native = root / 'target/sdk/aarch64/bin'
            native.mkdir(parents=True)
            (native / 'hyper-cargo').write_text(recorder)
            (native / 'hyper-cargo').chmod(0o755)
            output = root / 'probe.json'
            env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ['PATH'], PROBE=str(output))
            env.pop('HYPER_CONFIG', None)
            for workspace in ('kernel', 'tools/fit-pack', 'app',
                              'sdk/toolchain/tests/std-smoke', 'sdk/toolchain/tests/rust-smoke'):
                cwd = root / workspace
                cwd.mkdir(parents=True)
                subprocess.run(['sh', str(script)], cwd=cwd, env=env, check=True)
                result = json.loads(output.read_text())
                if workspace in ('kernel', 'tools/fit-pack'):
                    self.assertNotIn('--config', result['argv'])
                    self.assertEqual(result['config'], str(root / 'kernel/configs/qemu_aarch64_defconfig'))
                else:
                    index = result['argv'].index('--config')
                    self.assertEqual(result['argv'][index + 1], str(root / '.vscode/rust-analyzer.toml'))
                    self.assertEqual(result['std'], '0' if workspace.endswith('rust-smoke') else '1')
            env['HYPER_CONFIG'] = '/chosen/board.config'
            subprocess.run(['sh', str(script)], cwd=root / 'kernel', env=env, check=True)
            self.assertEqual(json.loads(output.read_text())['config'], '/chosen/board.config')


if __name__ == '__main__':
    unittest.main()
