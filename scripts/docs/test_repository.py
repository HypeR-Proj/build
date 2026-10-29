# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import unittest
from mkdocs.exceptions import PluginError
from repository_site import resolve


class RepositoryLinks(unittest.TestCase):
    def setUp(self):
        self.settings = {'repository': 'kernel', 'revision': 'a' * 40,
                         'pages': {'README.md': 'index.md', 'docs/guide.md': 'docs/guide.md'},
                         'assets': ['logo.png'],
                         'files': ['README.md', 'docs/guide.md', 'src/lib.rs', 'logo.png']}

    def test_readme_and_source_links_survive_staging(self):
        self.assertEqual(resolve('../README.md#start', 'docs/guide.md', self.settings), '../index.md#start')
        self.assertEqual(resolve('docs/guide.md', 'index.md', self.settings), 'docs/guide.md')
        self.assertEqual(resolve('../src/lib.rs#L3', 'docs/guide.md', self.settings),
                         'https://github.com/HypeR-Proj/kernel/blob/' + 'a' * 40 + '/src/lib.rs#L3')

    def test_unowned_and_missing_links_fail_instead_of_pointing_at_a_wrong_repo(self):
        for target in ('../../abi/README.md', '../missing.md'):
            with self.subTest(target=target), self.assertRaises(PluginError):
                resolve(target, 'docs/guide.md', self.settings)

    def test_explicit_cross_repository_and_fragment_links_are_preserved(self):
        for url in ('https://hyper-proj.github.io/abi/docs/native.html', '#contract'):
            self.assertEqual(resolve(url, 'docs/guide.md', self.settings), url)


if __name__ == '__main__':
    unittest.main()
