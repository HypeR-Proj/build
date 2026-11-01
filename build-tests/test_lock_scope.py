# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""A provider upgrade must not require edits to consumer manifests or locks."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from buildsystem import lock_scope
from buildsystem.link_context import preview
from buildsystem.shared_plan import merge_locks
from buildsystem.toml import dumps


@unittest.skipUnless(shutil.which('cargo'), 'Cargo is required for dependency-resolution regression tests')
class LockOwnership(unittest.TestCase):
    def test_registry_owner_version_replaces_only_the_generated_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.cargo').mkdir()
            (root / '.cargo/config.toml').write_text(
                '[source.crates-io]\nreplace-with="fixture"\n'
                f'[source.fixture]\ndirectory={json.dumps(str(root / "vendor"))}\n')
            for version in ('1.0.0', '1.1.0'):
                source = root / 'vendor' / ('implementation-' + version)
                (source / 'src').mkdir(parents=True)
                (source / 'src/lib.rs').write_text('pub fn available() {}\n')
                (source / '.cargo-checksum.json').write_text('{"files":{},"package":null}')
                (source / 'Cargo.toml').write_text(
                    f'[package]\nname="implementation"\nversion="{version}"\nedition="2024"\n')

            def recipe(name, version):
                destination = root / name
                (destination / 'src').mkdir(parents=True)
                (destination / 'src/lib.rs').write_text('')
                manifest = destination / 'Cargo.toml'
                manifest.write_text(f'[package]\nname="{name}"\nversion="1.0.0"\nedition="2024"\n'
                                    f'[workspace]\n[dependencies]\nimplementation="{version}"\n')
                return manifest

            def resolve(manifest):
                subprocess.run(['cargo', 'fetch', '--offline', '--manifest-path', str(manifest)],
                               cwd=root, check=True, capture_output=True)
                return tomllib.loads(manifest.with_name('Cargo.lock').read_text())

            client = recipe('client', '=1.0.0')
            old = resolve(client)
            client.write_text(client.read_text().replace('=1.0.0', '1'))
            original = client.with_name('Cargo.lock').read_bytes()
            provider = resolve(recipe('provider', '=1.1.0'))
            recipe('canonical', '=1.1.0')
            context = recipe('context', '1')
            with context.open('a') as stream:
                stream.write('canonical = { path="../canonical" }\n')
            merged = merge_locks([old, provider])
            context.with_name('Cargo.lock').write_text(dumps(lock_scope.prefer(merged, provider)))
            result = resolve(context)
            self.assertEqual([entry['version'] for entry in result['package']
                              if entry['name'] == 'implementation'], ['1.1.0'])
            self.assertEqual(client.with_name('Cargo.lock').read_bytes(), original)
            self.assertEqual([entry['version'] for entry in old['package']
                              if entry['name'] == 'implementation'], ['1.0.0'])

    def test_provider_upgrade_preserves_consumers_but_not_direct_dependency_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {**os.environ, 'RUSTC_BOOTSTRAP': '1'}
            cargo = shutil.which('cargo')

            def create(name, dependencies='', version='1.0.0'):
                path = root / name
                (path / 'src').mkdir(parents=True, exist_ok=True)
                (path / 'src/lib.rs').write_text('pub fn available() {}\n')
                (path / 'Cargo.toml').write_text(
                    f'[package]\nname="{name}"\nversion="{version}"\nedition="2024"\n'
                    f'[workspace]\n[dependencies]\n{dependencies}')
                return path / 'Cargo.toml'

            private = create('implementation')
            library = create('library', 'implementation = { path = "../implementation" }\n')
            client = create('client', 'library = { path = "../library" }\n')
            direct = create('direct', 'library = { path = "../library" }\n'
                            'implementation = { path = "../implementation" }\n')

            def run(*args):
                return subprocess.check_output([cargo, *args], cwd=root, env=environment, stderr=subprocess.PIPE)

            for manifest in (library, client, direct):
                run('generate-lockfile', '--offline', '--manifest-path', str(manifest))
            before = {manifest: (manifest.read_bytes(), manifest.with_name('Cargo.lock').read_bytes())
                      for manifest in (client, direct)}
            create('implementation', version='1.1.0')
            run('generate-lockfile', '--offline', '--manifest-path', str(library))

            for manifest in (client, direct):
                context = preview(manifest, manifest, root / ('view-' + manifest.parent.name), cargo,
                                  environment, 'provider-upgraded')
                graph = json.loads(run('build', '--offline', '--locked', '--manifest-path', str(context),
                                       '-Z', 'unstable-options', '--unit-graph'))
                old = tomllib.loads(before[manifest][1].decode())
                resolved = tomllib.loads(context.with_name('Cargo.lock').read_text())
                packages = lock_scope.owned(graph, {'library'})
                if manifest == direct:
                    with self.assertRaisesRegex(ValueError, 'repository-owned locked dependency: implementation'):
                        lock_scope.validate(old, resolved, packages)
                else:
                    lock_scope.validate(old, resolved, packages)
                    # The provider owns its complete private closure instead.
                    with self.assertRaisesRegex(ValueError, 'repository-owned locked dependency: implementation'):
                        lock_scope.validate(old, resolved, lock_scope.owned(graph, {'library'}, owner='library'))
                self.assertEqual(before[manifest],
                                 (manifest.read_bytes(), manifest.with_name('Cargo.lock').read_bytes()))


if __name__ == '__main__':
    unittest.main()
