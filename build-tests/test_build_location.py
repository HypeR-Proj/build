# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise a repository-free source root and independently located build tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from buildsystem.model import discover, find_workspace


class BuildLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hyper-build-location-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'source'
        self.output = self.root.parent / 'common-output'
        self.root.mkdir()
        self.tools = self.root / 'tools/build-system'
        self.tools.mkdir(parents=True)
        owners = discover(find_workspace(ROOT))
        source_build = owners['build'].source
        for name in ('Makefile', 'project.py', 'mk/entry.mk', 'scripts/build.py'):
            target = self.tools / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_build / name, target)
        shutil.copytree(source_build / 'scripts/buildsystem', self.tools / 'scripts/buildsystem')
        (self.tools / 'mk/backend.mk').write_text('probe:\n\t@echo prepared-backend\n')
        (self.tools / 'component.json').write_text(json.dumps({
            'version': 1, 'name': 'build', 'build': {'makefile': 'mk/backend.mk'},
            'workspace': {'files': {'scripts': 'scripts'}}}))
        self.module = self.root / 'modules/alpha'
        self.module.mkdir(parents=True)
        # Use a shipped module frontend, including its entry discovery logic.
        source = owners['kernel'].source / 'Makefile'
        shutil.copy2(source, self.module / 'Makefile')
        (self.module / 'component.json').write_text(json.dumps({
            'version': 1, 'name': 'alpha', 'build': {'steps': [[
                sys.executable, '{source}/emit.py', '{out}']]}}))
        (self.module / 'emit.py').write_text(
            'from pathlib import Path\nimport sys\n'
            'p=Path(sys.argv[1]);p.mkdir(parents=True,exist_ok=True)\n'
            '(p/"result").write_text("built")\n')
        manifest = self.root / '.repo/manifests'
        manifest.mkdir(parents=True)
        (manifest / 'component.json').write_text(json.dumps({'version': 1, 'name': 'manifest'}))
        (manifest / 'default.xml').write_text('''<manifest>
          <project name="build" path="tools/build-system">
            <linkfile src="Makefile" dest="Makefile"/>
            <linkfile src="project.py" dest="project.py"/>
          </project>
          <project name="alpha" path="modules/alpha"/>
        </manifest>''')
        for name in ('Makefile', 'project.py'):
            (self.root / name).symlink_to('tools/build-system/' + name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('HYPER_', 'MAKE')) and key not in ('OUT', 'MODULE', 'MFLAGS')}

    def command(self, cwd, *args):
        return subprocess.check_output(args, cwd=cwd, env=self.env, text=True)

    def test_root_tools_and_module_share_output_without_a_root_repository(self):
        self.command(self.root, 'make', 'prepare', f'OUT={self.output}')
        state = self.output / 'state/aarch64/prepare.json'
        timestamp = state.stat().st_mtime_ns
        self.command(self.tools, 'make', 'prepare', f'OUT={self.output}')
        self.assertEqual(state.stat().st_mtime_ns, timestamp)
        self.assertIn('prepared-backend', self.command(self.root, 'make', 'probe', f'OUT={self.output}'))
        self.command(self.module, 'make', f'OUT={self.output}')
        self.assertEqual((self.output / 'build/alpha/result').read_text(), 'built')
        self.assertFalse((self.root / '.git').exists())
        self.assertFalse((self.tools / 'out').exists())
        self.assertFalse((self.module / 'out').exists())
        self.assertEqual(self.command(self.tools, sys.executable, 'project.py', 'root').strip(), str(self.root))
        self.command(self.module, 'make', 'prepare')
        self.assertTrue((self.root / 'out/workspace-owner.json').is_file())

    def test_exported_workspace_preserves_entry_links_and_prepares_without_git(self):
        archive = self.root.parent / 'source.tar.xz'
        self.command(self.root, sys.executable, 'project.py', '--out', str(self.output), 'archive', str(archive))
        extracted = self.root.parent / 'extracted'
        with tarfile.open(archive) as stream:
            stream.extractall(extracted, filter='data')
        restored = extracted / 'HypeR'
        self.assertTrue((restored / 'Makefile').is_symlink())
        self.command(restored, 'make', 'prepare')
        self.command(restored / 'modules/alpha', 'make')
        self.assertEqual((restored / 'out/build/alpha/result').read_text(), 'built')

    def test_source_documentation_uses_docs_owner_without_preparation(self):
        checker = self.root / 'docs/tools/check-source.py'
        checker.parent.mkdir(parents=True)
        shutil.copy2(discover(find_workspace(ROOT))['docs'].source / 'tools/check-source.py', checker)
        (self.root / 'docs/component.json').write_text(json.dumps({'name': 'docs', 'version': 1}))
        manifest = self.root / '.repo/manifests/default.xml'
        manifest.write_text(manifest.read_text().replace('</manifest>', '<project name="docs" path="docs"/></manifest>'))
        readme = self.module / 'README.md'
        readme.write_text('[Metadata](component.json)\n')
        result = self.command(self.root, sys.executable, 'project.py', 'check-docs', str(self.module))
        self.assertIn('Markdown source links are valid', result)
        readme.write_text('[Missing](missing.md)\n')
        result = subprocess.run([sys.executable, 'project.py', 'check-docs', str(self.module)],
            cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing local link', result.stderr)
        self.assertFalse((self.root / 'out').exists())
        self.assertFalse((self.tools / 'out').exists())

    def test_exported_entry_cannot_replace_provenance_or_source_directories(self):
        manifest = self.root / '.repo/manifests/default.xml'
        original = manifest.read_text()
        archive = self.root.parent / 'source.tar.xz'
        archive.write_bytes(b'previous archive')
        for destination in ('source-inventory.json', 'modules', 'project.py/child'):
            with self.subTest(destination=destination):
                manifest.write_text(original.replace('</project>',
                    f'<linkfile src="project.py" dest="{destination}"/></project>', 1))
                result = subprocess.run([sys.executable, 'project.py', 'archive', str(archive)],
                    cwd=self.root, env=self.env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid exported manifest entry', result.stderr)
                self.assertEqual(archive.read_bytes(), b'previous archive')


if __name__ == '__main__':
    unittest.main()
