#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check deployment profiles, overrides and timestamp-preserving installation."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/app-deployment.py'
MANIFEST = ROOT / 'app/deployment.json'
spec = importlib.util.spec_from_file_location('deployment', SCRIPT)
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


class DeploymentTests(unittest.TestCase):
    def test_system_retains_apps_without_acceptance_programs(self):
        roots = dict(apps='/apps', sdk='/sdk', std='/std', arch='riscv64')
        system = deployment.compose(MANIFEST, 'system', roots)
        development = deployment.compose(MANIFEST, 'development', roots)
        system_names = set(system[1::3])
        self.assertTrue({'init', 'bin/sh', 'bin/cp', 'bin/vmm', 'svc/vm-manager',
                         'lib/ld-hyper-riscv64.so', 'lib/libhyper.so'} <= system_names)
        self.assertTrue(system_names < set(development[1::3]))
        for fixture in ('bin/echo-static', 'bin/dynamic-test', 'bin/std-test', 'bin/std-test-static'):
            self.assertNotIn(fixture, system_names)
            self.assertIn(fixture, development[1::3])
        replaced = deployment.compose(MANIFEST, 'system', roots, ['bin/ps=/probe'])
        self.assertEqual(replaced[replaced.index('bin/ps') + 1], '/probe')
        with self.assertRaises(ValueError):
            deployment.compose(MANIFEST, 'system', roots, ['bin/typo=/probe'])

    def test_invalid_manifest_fails_before_installation(self):
        original = json.loads(MANIFEST.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for field, value in [('destination', '../escape'), ('mode', '0999'),
                                 ('binary', 'bad;command'), ('profiles', ['typo'])]:
                with self.subTest(field=field):
                    data = json.loads(json.dumps(original))
                    data['entries'][0][field] = value
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        deployment.load(path)
            original['entries'].append(original['entries'][0])
            path.write_text(json.dumps(original))
            with self.assertRaises(ValueError):
                deployment.load(path)

    def test_install_reuses_outputs_and_includes_io_service(self):
        with tempfile.TemporaryDirectory(prefix='hyper deployment ') as directory:
            root = Path(directory)
            build, output = root / 'build', root / 'output'
            build.mkdir()
            programs = [entry for entry in deployment.load(MANIFEST) if 'binary' in entry]
            self.assertIn('hyper-io-runtime', [entry['binary'] for entry in programs])
            self.assertNotIn('hyper-io-smoke', [entry['binary'] for entry in programs])
            for entry in programs:
                (build / entry['binary']).write_text(entry['binary'])
            command = [sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                       '--build', str(build), '--output', str(output)]
            subprocess.run(command, check=True)
            before = {path.name: path.stat().st_mtime_ns for path in output.iterdir()}
            subprocess.run(command, check=True)
            self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in output.iterdir()})
            self.assertEqual((output / 'sh').read_text(), 'hyper-shell')
            self.assertEqual((output / 'sh').stat().st_mode & 0o777, 0o755)


if __name__ == '__main__':
    unittest.main()
