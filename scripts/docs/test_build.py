# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Verify that independent Native providers cannot overwrite API documentation."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build


class NativeDocumentationTests(unittest.TestCase):
    def test_boot_and_formal_crates_with_identical_names_keep_distinct_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output'
            api = output / 'api'
            api.mkdir(parents=True)
            environments = {'app': ('system', 'formal-tool'),
                            'bootstrap/stage1': ('bootstrap', 'bootstrap-tool')}

            def packages(manifest):
                workspace = manifest.parent.relative_to(root).as_posix()
                if workspace not in environments:
                    return []
                _, binary = environments[workspace]
                result = [{'name': binary, 'manifest_path': str(manifest), 'targets': [
                    {'name': 'same_provider', 'kind': ['lib'], 'doc': True,
                     'src_path': str(manifest.parent / 'src/lib.rs')},
                    {'name': binary, 'kind': ['bin'], 'doc': True,
                     'src_path': str(manifest.parent / 'src/main.rs')},
                ]}]
                if workspace == 'app':
                    result.append({'name': 'test-fixture',
                                   'manifest_path': str(root / 'tests/app/fixture/Cargo.toml'),
                                   'targets': [{'name': 'test-fixture', 'kind': ['bin'], 'doc': True,
                                                'src_path': str(root / 'tests/app/fixture/src/main.rs')}]})
                return result

            def generate(command, *, env=None):
                if command[0] == 'make':
                    return
                self.assertNotIn('test-fixture', command)
                workspace = str(Path(command[command.index('--manifest-path') + 1]).parent)
                environment, binary = environments[workspace]
                architecture = Path(env['CARGO_TARGET_DIR']).parent.name
                self.assertEqual(env['HYPER_LINK_MODE'],
                                 'static' if environment == 'bootstrap' else 'dynamic')
                docs = Path(env['CARGO_TARGET_DIR']) / f'{architecture}-unknown-hyper/doc'
                name = binary.replace('-', '_') if '--bin' in command else 'same_provider'
                page = docs / name / 'index.html'
                page.parent.mkdir(parents=True)
                page.write_text(f'<p>{architecture}/{environment}</p>')
                if '--bin' in command:
                    page.write_text('<a class="src" href="../src/shared_owner/lib.rs.html#22">Source</a>')
                else:
                    source = docs / 'src/shared_owner/lib.rs.html'
                    source.parent.mkdir(parents=True)
                    source.write_text('<a id="22"></a>')

            with patch.object(build, 'ROOT', root), patch.object(build, 'OUTPUT', output), \
                    patch.object(build, 'workspace_packages', side_effect=packages), \
                    patch.object(build, 'run', side_effect=generate):
                build.native_documentation(api)
            for architecture in ('aarch64', 'riscv64'):
                for environment, binary in environments.values():
                    reference = api / 'native' / architecture / environment
                    self.assertEqual((reference / 'same_provider/index.html').read_text(),
                                     f'<p>{architecture}/{environment}</p>')
                    program = reference / 'programs' / binary.replace('-', '_') / 'index.html'
                    self.assertIn('href="../../src/shared_owner/lib.rs.html#22"', program.read_text())


if __name__ == '__main__':
    unittest.main()
