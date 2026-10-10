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
MANIFEST = ROOT / 'mk/components.mk'
spec = importlib.util.spec_from_file_location('deployment', SCRIPT)
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


class DeploymentTests(unittest.TestCase):
    def test_registry_checks_cargo_targets_and_derives_host_exclusions(self):
        entries = deployment.load(MANIFEST)
        result = subprocess.check_output([sys.executable, str(SCRIPT), 'host-excludes',
                                          '--manifest', str(MANIFEST)], text=True)
        self.assertEqual(result.split(), [value for entry in entries if 'library' in entry
                                         for value in ('--exclude', entry['package'])])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.mk'
            for record in ('binary|app/echo|missing-binary|echo|bin/echo|system',
                           'library|app/echo|libhyper_echo.so|lib/libhyper_echo.so|lib/libhyper_echo.so|system',
                           'binary|../outside|escape|escape|bin/escape|system'):
                path.write_text("component-records:\n\t@printf '%s\\n' '" + record + "'\n")
                with self.subTest(record=record), self.assertRaises(ValueError):
                    deployment.load(path)

    def test_system_retains_apps_without_acceptance_programs(self):
        roots = dict(apps='/apps', sdk='/sdk', std='/std', arch='riscv64')
        system = deployment.compose(MANIFEST, 'system', roots)
        development = deployment.compose(MANIFEST, 'development', roots)
        system_names = set(system[1::3])
        link = system.index('lib')
        self.assertEqual(system[link - 1:link + 2], ['symlink', 'lib', 'lib64'])
        self.assertTrue({'init', 'bin/sh', 'bin/cp', 'bin/vmm', 'bin/ldd', 'svc/vm-manager',
                         'lib', 'lib64/ld-hyper-riscv64.so',
                         'lib64/riscv64-hyper-hyper/libhyper.so'} <= system_names)
        # Rust libraries are selected from DT_NEEDED after executable overrides.
        self.assertNotIn('lib64/riscv64-hyper-hyper/libhyper_clap_shared.so', system_names)
        libraries = [entry for entry in deployment.load(MANIFEST) if 'library' in entry]
        self.assertIn('libhyper_clap_shared.so', [entry['library'] for entry in libraries])
        self.assertTrue(system_names < set(development[1::3]))
        for fixture in ('bin/echo-static', 'bin/dynamic-test', 'bin/std-test', 'bin/std-test-static'):
            self.assertNotIn(fixture, system_names)
            self.assertIn(fixture, development[1::3])
        replaced = deployment.compose(MANIFEST, 'system', roots, ['bin/ps=/probe'])
        self.assertEqual(replaced[replaced.index('bin/ps') + 1], '/probe')
        with self.assertRaises(ValueError):
            deployment.compose(MANIFEST, 'system', roots, ['bin/typo=/probe'])

    def test_invalid_manifest_fails_before_installation(self):
        original = {'version': 1, 'entries': deployment.load(MANIFEST)}
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
            for entry in deployment.load(MANIFEST):
                if 'library' in entry:
                    (build / entry['library']).write_text(entry['library'])
            command = [sys.executable, str(SCRIPT), 'install', '--manifest', str(MANIFEST),
                       '--build', str(build), '--output', str(output)]
            subprocess.run(command, check=True)
            before = {path.name: path.stat().st_mtime_ns for path in output.iterdir()}
            subprocess.run(command, check=True)
            self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in output.iterdir()})
            self.assertEqual((output / 'sh').read_text(), 'hyper-shell')
            self.assertEqual((output / 'sh').stat().st_mode & 0o777, 0o755)
            self.assertEqual((output / 'lib/libhyper_tool_args_shared.so').read_text(),
                             'libhyper_tool_args_shared.so')

    def test_library_artifacts_require_exact_safe_names_and_unique_sources(self):
        original = {'version': 1, 'entries': deployment.load(MANIFEST)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for name in ('../libbad.so', 'libbad.so;command', 'bad.so', 'libbad.a'):
                data = json.loads(json.dumps(original))
                library = next(entry for entry in data['entries'] if 'library' in entry)
                library['library'] = name
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    deployment.load(path)
            library = next(entry for entry in original['entries'] if 'library' in entry)
            library['source'] = '/ambiguous'
            path.write_text(json.dumps(original))
            with self.assertRaises(ValueError):
                deployment.load(path)

    def test_symlink_manifest_requires_a_relative_target_and_cannot_be_replaced_as_a_file(self):
        roots = dict(apps='/apps', sdk='/sdk', std='/std', arch='aarch64')
        with self.assertRaises(ValueError):
            deployment.compose(MANIFEST, 'system', roots, ['lib=/host/regular-file'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            for target in ('.', '../lib64', '/lib64', 'lib64//other', 'bad\0path'):
                data = {'version': 1, 'entries': deployment.load(MANIFEST)}
                next(entry for entry in data['entries'] if 'symlink' in entry)['symlink'] = target
                path.write_text(json.dumps(data))
                with self.subTest(target=target), self.assertRaises(ValueError):
                    deployment.load(path)


if __name__ == '__main__':
    unittest.main()
