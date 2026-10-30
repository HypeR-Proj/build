# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import tempfile
import unittest

from catalog import render


class CatalogTests(unittest.TestCase):
    def test_adding_a_manifest_module_changes_navigation_without_a_top_level_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / 'default.xml'
            manifest.write_text('<manifest><project name="kernel" path="kernel"/></manifest>')
            before = render(root, 'default.xml', {'integration': {'repositories': ['HypeR']}})
            self.assertIn('https://hyper-proj.github.io/kernel/', before)
            self.assertIn('not part of the source checkout', before)
            manifest.write_text('<manifest><project name="kernel" path="kernel"/>'
                                '<include name="modules.xml"/></manifest>')
            (root / 'modules.xml').write_text('<manifest><project name="lib-example" path="lib/example"/></manifest>')
            after = render(root, 'default.xml', {})
            self.assertIn('https://hyper-proj.github.io/lib-example/', after)

    def test_includes_cannot_recurse_or_escape_the_manifest_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for include in ('default.xml', '../outside.xml'):
                (root / 'default.xml').write_text(f'<manifest><include name="{include}"/></manifest>')
                with self.assertRaises(ValueError):
                    render(root, 'default.xml', {})


if __name__ == '__main__':
    unittest.main()
