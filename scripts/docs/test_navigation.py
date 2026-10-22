# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check guide placement, navigation coverage and published URL migrations."""

import json
from pathlib import Path
import tempfile
import unittest

from check_links import References, check_site
from doxygen import fix_index_anchor
from navigation import navigation
from redirects import write_redirects


def leaves(entries):
    for entry in entries:
        for value in entry.values():
            if isinstance(value, list):
                yield from leaves(value)
            else:
                yield value


class DocumentationStructure(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_categories_cover_each_page_once_and_preserve_application_hierarchy(self):
        pages = ['README.md', 'docs/README.md', 'docs/learn/README.md',
                 'docs/learn/getting-started.md', 'docs/architecture/overview.md',
                 'kernel/docs/architecture/architecture.md', 'docs/design/storage/fat.md',
                 'kernel/docs/design/vfs.md', 'docs/usage/README.md',
                 'docs/usage/applications/README.md', 'docs/usage/applications/less.md',
                 'docs/project/status.md', 'CONTRIBUTING.md', 'SECURITY.md',
                 'app/README.md', 'bootstrap/stage1/README.md', 'sdk/abi/docs/native.md',
                 'tests/app/README.md', 'kernel/tests/VALIDATION.md']
        for name in pages:
            self.write(name, '# ' + Path(name).stem)
        nav = navigation(self.root, pages)
        self.assertCountEqual(leaves(nav), pages + ['api/index.md'])
        sections = {key: value for entry in nav for key, value in entry.items()}
        self.assertIn('kernel/docs/design/vfs.md', list(leaves(sections['Detailed design'])))
        usage = sections['Usage']
        self.assertEqual(usage[0], {'README': 'docs/usage/README.md'})
        applications = next(entry['Applications'] for entry in usage if 'Applications' in entry)
        self.assertEqual(list(leaves(applications)), ['docs/usage/applications/README.md',
                                                    'docs/usage/applications/less.md'])
        self.assertEqual(nav, navigation(self.root, list(reversed(pages))))

    def test_new_guides_must_choose_a_documentation_category(self):
        for name in ('docs/uncategorized.md', 'docs/misc/new.md', 'kernel/docs/misc.md'):
            with self.subTest(name=name):
                self.write(name, '# New')
                with self.assertRaisesRegex(ValueError, 'purpose directory'):
                    navigation(self.root, [name])

    def test_old_url_keeps_relative_target_and_split_section_destinations(self):
        site = self.root / 'site'
        self.write('site/index.html', '<html></html>')
        self.write('site/assets/redirect.js', '')
        self.write('site/docs/usage/applications/index.html', '<h1>Applications</h1>')
        self.write('site/docs/usage/applications/free/index.html', '<h2 id="accounting">Free</h2>')
        manifest = self.write('redirects.json', json.dumps({'pages': {
            'docs/applications.md': {'target': 'docs/usage/applications/README.md',
                                     'anchors': {'memory': 'docs/usage/applications/free.md#accounting'}}}}))
        write_redirects(site, manifest)
        html = (site / 'docs/applications/index.html').read_text()
        self.assertIn('../usage/applications/index.html', html)
        self.assertIn('../usage/applications/free/index.html#accounting', html)
        self.assertIn('noindex', html)
        self.assertIn('../../assets/redirect.js', References(html).links)
        check_site(site)

    def test_redirects_reject_missing_targets_and_overwriting_real_pages(self):
        site = self.root / 'site'
        self.write('site/docs/current/index.html', '')
        for old, target, message in [('docs/old.md', 'docs/missing.md', 'missing'),
                                      ('docs/current.md', 'docs/current.md', 'overwrite'),
                                      ('../outside.md', 'docs/current.md', 'invalid')]:
            with self.subTest(old=old):
                manifest = self.write('redirects.json', json.dumps({'pages': {old: {'target': target}}}))
                with self.assertRaisesRegex(ValueError, message):
                    write_redirects(site, manifest)

    def test_move_to_a_section_readme_preserves_the_existing_published_url(self):
        page = self.write('site/kernel/docs/architecture/index.html', '<h1>Architecture</h1>')
        manifest = self.write('redirects.json', json.dumps({'pages': {
            'kernel/docs/architecture.md': {'target': 'kernel/docs/architecture/README.md'}}}))
        write_redirects(self.root / 'site', manifest)
        self.assertEqual(page.read_text(), '<h1>Architecture</h1>')

    def test_doxygen_single_letter_jump_targets_the_matching_function_list(self):
        bar = '<div class="qindex"><a class="qindex" href="#index_h">H</a></div>\n'
        page = bar + '<ul><li>hyper_one()</li><li>hyper_two()</li></ul>'
        fixed = fix_index_anchor(page)
        self.assertIn('index_h', References(fixed).anchors)
        self.assertEqual(fixed, fix_index_anchor(fixed))
        unrelated = bar + '<ul><li>another_function()</li></ul>'
        self.assertEqual(unrelated, fix_index_anchor(unrelated))
        existing = '<a id="index_h"></a>' + page
        self.assertEqual(existing, fix_index_anchor(existing))


if __name__ == '__main__':
    unittest.main()
