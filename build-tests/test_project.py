# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import project


class WorkspaceTests(unittest.TestCase):
    def test_paths_cannot_escape_or_address_git_internals(self):
        for path in ('../kernel', '/tmp/source', 'x/../../outside', '.git/config', '.repo/config'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                project.relative(path)

    def test_nested_projects_and_links_are_included_without_source_rewrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, relative in [('build', '.'), ('kernel', 'kernel'), ('manifest', '.workspace')]:
                directory = root / relative
                directory.mkdir(exist_ok=True)
                (directory / 'component.json').write_text(json.dumps({'name': name, 'version': 1}))
                (directory / 'source.txt').write_text(name)
                subprocess.run(['git', 'init', '-q', str(directory)], check=True)
                subprocess.run(['git', 'add', 'component.json', 'source.txt'], cwd=directory, check=True)
                subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                                'commit', '-qm', 'fixture'], cwd=directory, check=True)
            (root / '.workspace/default.xml').write_text('''<manifest>
                <project name="build" path="."/>
                <project name="kernel" path="kernel"/>
                <project name="manifest" path=".workspace">
                    <linkfile src="source.txt" dest="config.txt"/>
                </project></manifest>''')
            original = (root / 'kernel/source.txt').stat().st_mtime_ns
            commits = {n: project.git(p, 'rev-parse', 'HEAD') for n, (p, _) in project.components(root).items()}
            build_output = root / 'out'
            view = project.prepare(root, build_output)
            self.assertEqual((root / 'kernel/source.txt').stat().st_mtime_ns, original)
            self.assertEqual(commits, {n: project.git(p, 'rev-parse', 'HEAD') for n, (p, _) in project.components(root).items()})
            self.assertFalse((root / 'out/system').exists())
            self.assertFalse((root / 'config.txt').exists())
            self.assertEqual((view / 'config.txt').read_text(), 'manifest')
            state = root / 'out/state/aarch64/prepare.json'
            unchanged = {p: p.stat().st_mtime_ns for p in (state, view / 'config.txt', view / 'component.json')}
            self.assertEqual(project.prepare(root, build_output), view)
            self.assertEqual(unchanged, {p: p.stat().st_mtime_ns for p in unchanged})
            output = root / 'sources.tar.xz'
            project.archive(root, output, build_output)
            with tarfile.open(output) as archive:
                self.assertEqual(archive.extractfile('HypeR/kernel/source.txt').read(), b'kernel')
                self.assertEqual(archive.extractfile('HypeR/.workspace/source.txt').read(), b'manifest')
                self.assertNotIn('HypeR/config.txt', archive.getnames())
                self.assertFalse(any('/out/' in name for name in archive.getnames()))
                self.assertIn('HypeR/source-inventory.json', archive.getnames())


if __name__ == '__main__':
    unittest.main()
