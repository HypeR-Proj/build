# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Behavioral fixtures for source links and the final Pages artifact."""

import importlib.util
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from build import (DOC_FLAGS, documented_crates, program_doc_flags,
                   reference_doc_flags, workspace_packages)
from check_links import References, check_site
from markdown import markdown
from mkdocs.config import load_config
from mkdocs.structure.files import File, Files
from pymdownx.superfences import fence_div_format
from rustdoc import fix_rustdoc_links
from types import SimpleNamespace
from xml.etree import ElementTree

# `site` is also the Python interpreter's startup module.
spec = importlib.util.spec_from_file_location('documentation_site', Path(__file__).with_name('site.py'))
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


class GuideContent(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.inline_code = []
        self.block_code = []
        self.checkboxes = []
        self.in_pre = False
        self.in_code = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.in_pre = True
            self.block_code.append('')
        elif tag == 'code':
            self.in_code = True
            if not self.in_pre:
                self.inline_code.append('')
        elif tag == 'input':
            self.checkboxes.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.in_pre = False
        elif tag == 'code':
            self.in_code = False

    def handle_data(self, data):
        if self.in_pre:
            self.block_code[-1] += data
        elif self.in_code:
            self.inline_code[-1] += data


class DocumentationLinks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def write(self, path, text):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def render_guide(self, text):
        self.write('input/guide.md', text)
        self.write('manifest.json', json.dumps({
            'revision': 'abcdef', 'sources': ['guide.md'], 'nav': [{'Guide': 'guide.md'}],
        }))
        config = load_config(str(Path(__file__).with_name('mkdocs.yml')),
                             docs_dir=str(self.root / 'input'), site_dir=str(self.root / 'output'))
        site.on_config(config)
        page = SimpleNamespace(file=File('guide.md', config.docs_dir, config.site_dir, True))
        site.on_page_markdown(text, page)
        return GuideContent(markdown(
            text, extensions=config.markdown_extensions,
            extension_configs=config.mdx_configs))

    def test_inline_code_wraps_as_spaces_without_changing_code_blocks(self):
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                article = self.render_guide(newline.join([
                    'Run `handle', '--objects` or ``echo  `literal`', '<value>``.', '',
                    '```sh', 'handle', '--objects', '```', '',
                    '    echo  one', '    echo two', '',
                ]))
                self.assertEqual(article.inline_code,
                                 ['handle --objects', 'echo  `literal` <value>'])
                self.assertEqual(article.block_code, ['handle\n--objects', 'echo  one\necho two\n'])

    def test_task_lists_keep_completion_state_and_code_examples_literal(self):
        article = self.render_guide('- [ ] Pending\n- [x] Done\n- [X] Also done\n\n'
                                    '```text\n- [x] Literal example\n```\n')
        boxes = article.checkboxes
        self.assertEqual(len(boxes), 3)
        self.assertEqual([node.get('type') for node in boxes], ['checkbox'] * 3)
        self.assertEqual(['checked' in node for node in boxes], [False, True, True])
        self.assertTrue(all('disabled' in node for node in boxes))
        self.assertEqual(article.block_code, ['- [x] Literal example'])

    def test_document_source_and_asset_links(self):
        self.write('kernel/src/task.rs', '// source')
        self.write('staged/docs/guide.md', '# Guide')
        self.write('staged/Logo image.png', '')
        sources = ['kernel/src/task.rs', 'docs/guide.md', 'Logo image.png']
        def resolve(url):
            return site.source_link(url, 'docs/start.md', self.root, sources,
                                    'abcdef', self.root / 'staged')
        self.assertEqual(resolve('guide.md#section'), 'guide.md#section')
        self.assertEqual(resolve('../Logo%20image.png'), '../Logo%20image.png')
        self.assertEqual(resolve('../kernel/src/task.rs#L12'),
                         'https://github.com/roolrz/HypeR/blob/abcdef/kernel/src/task.rs#L12')
        self.assertEqual(resolve('../kernel/src/'),
                         'https://github.com/roolrz/HypeR/tree/abcdef/kernel/src')

    def test_missing_source_is_rejected(self):
        with self.assertRaisesRegex(site.PluginError, 'missing or unpublished'):
            site.source_link('../gone.rs', 'docs/start.md', self.root, [], 'abc', self.root / 'staged')

    def test_unpublished_source_and_escape_are_rejected(self):
        self.write('.agent/design.md', 'private')
        for link in ('../.agent/design.md', '../../outside.md'):
            with self.subTest(link=link), self.assertRaises(site.PluginError):
                site.source_link(link, 'docs/start.md', self.root, [], 'abc', self.root / 'staged')

    def test_site_checks_quoted_paths_anchors_and_project_prefix(self):
        self.write('index.html', '<a href="/HypeR/api/a%20b.html?x=1#item">API</a>')
        self.write('api/a b.html', '<h1 id="item">API</h1><a href="../">Home</a>')
        check_site(self.root)

    def test_broken_file_and_fragment_fail(self):
        self.write('index.html', '<a href="missing.html">Missing</a><a href="#gone">Anchor</a>')
        with self.assertRaises(ValueError) as failure:
            check_site(self.root)
        self.assertIn('missing file', str(failure.exception))
        self.assertIn('missing anchor', str(failure.exception))

    def test_missing_asset_is_rejected(self):
        self.write('index.html', '<script src="missing.js"></script>')
        with self.assertRaisesRegex(ValueError, 'missing file'):
            check_site(self.root)

    def test_mermaid_receives_diagram_text_without_a_code_wrapper(self):
        diagram = 'flowchart LR\n A --> B'
        html = markdown(f'```mermaid\n{diagram}\n```', extensions=['pymdownx.superfences'],
                        extension_configs={'pymdownx.superfences': {'custom_fences': [
                            {'name': 'mermaid', 'class': 'mermaid', 'format': fence_div_format}]}})
        node = ElementTree.fromstring(html)
        self.assertEqual(node.tag, 'div')
        self.assertEqual(node.attrib['class'], 'mermaid')
        self.assertEqual(list(node), [])
        self.assertEqual(node.text, diagram)

    def test_rustdoc_files_are_checked_without_requiring_dynamic_anchors_or_scripts(self):
        self.write('index.html', '<a href="api/rust/item.html#impl-T%3CU%3E">Type</a>'
                   '<a href="api/rust/item.html#12-15">Source</a>')
        self.write('api/rust/item.html', '<meta name="generator" content="rustdoc">'
                   '<div id="impl-T%3CU%3E"></div><a id="12"></a><a id="15"></a>'
                   '<a href="item.html#dynamic-anchor">Generated anchor</a>'
                   '<script src="optional-trait.js"></script>')
        check_site(self.root, rustdoc_roots=('api/rust',))
        self.write('index.html', '<a href="api/rust/item.html#missing">Broken anchor</a>')
        with self.assertRaisesRegex(ValueError, 'missing anchor'):
            check_site(self.root, rustdoc_roots=('api/rust',))
        self.write('index.html', '<a href="api/rust/missing.html">Missing API page</a>')
        with self.assertRaisesRegex(ValueError, 'missing file'):
            check_site(self.root, rustdoc_roots=('api/rust',))
        self.write('index.html', '<p>Home</p>')
        self.write('api/rust/item.html', '<a href="missing-source.html">Source</a>')
        with self.assertRaisesRegex(ValueError, 'missing file'):
            check_site(self.root, rustdoc_roots=('api/rust',))

    def test_rustdoc_markdown_license_remains_a_raw_asset(self):
        source = self.write('input/api/static.files/font-LICENSE.md', '# Font license\n')
        files = Files([File('api/static.files/font-LICENSE.md', str(self.root / 'input'),
                            str(self.root / 'output'), True)])
        config = SimpleNamespace(site_dir=str(self.root / 'output'))
        site.on_files(files, config)
        files.copy_static_files()
        self.assertEqual((self.root / 'output/api/static.files/font-LICENSE.md').read_bytes(),
                         source.read_bytes())

    def test_same_name_library_and_binary_keep_distinct_docs_and_working_type_links(self):
        manifest = self.write('fixture/Cargo.toml', '[package]\nname = "doc-probe"\n'
                              'version = "0.1.0"\nedition = "2024"\n[workspace]\n')
        self.write('fixture/src/lib.rs', '/// Library type.\npub struct Thing;\n')
        self.write('fixture/src/main.rs', '/// Executable entry using [`doc_probe::Thing`].\n'
                   'fn main() {}\nmod nested {\n/// Creates [`doc_probe::Thing`].\n'
                   'fn make() -> doc_probe::Thing { doc_probe::Thing }\n}\n')
        target = self.root / 'build'
        environment = os.environ | {'CARGO_TARGET_DIR': str(target), 'RUSTC_BOOTSTRAP': '1',
                                    'RUSTDOCFLAGS': DOC_FLAGS}
        command = ['cargo', 'doc', '--manifest-path', str(manifest), '--offline',
                   '--document-private-items', '--no-deps']
        subprocess.run(command + ['--lib'], env=environment, check=True, capture_output=True)
        libraries = self.root / 'references'
        shutil.copytree(target / 'doc', libraries)
        shutil.rmtree(target / 'doc')
        environment['RUSTDOCFLAGS'] = program_doc_flags(libraries)
        subprocess.run(command + ['--bin', 'doc-probe'], env=environment,
                       check=True, capture_output=True)
        programs = libraries / 'programs'
        shutil.copytree(target / 'doc', programs)
        library_type = libraries / 'doc_probe/struct.Thing.html'
        self.assertIn('Library type.', library_type.read_text())
        self.assertIn('Executable entry', (programs / 'doc_probe/fn.main.html').read_text())
        for name in ('fn.main.html', 'nested/fn.make.html'):
            page = programs / 'doc_probe' / name
            links = [url for url in References(page.read_text()).links
                     if url.endswith('/struct.Thing.html')]
            self.assertTrue(links)
            for url in links:
                self.assertEqual((page.parent / url).resolve(), library_type)

    def test_explicit_sdk_selection_keeps_local_links_without_vendor_docs(self):
        application = self.write('app/Cargo.toml', '[package]\nname = "doc-app"\n'
                                 'version = "0.1.0"\nedition = "2024"\n[workspace]\n'
                                 '[dependencies]\nlocal-sdk = { path = "../sdk" }\n')
        self.write('app/src/lib.rs', '/// Uses [`local_sdk::Thing`].\n'
                   'pub fn make() -> local_sdk::Thing { local_sdk::Thing }\n')
        sdk = self.write('sdk/Cargo.toml', '[package]\nname = "local-sdk"\n'
                         'version = "0.1.0"\nedition = "2024"\n[workspace]\n'
                         '[dependencies]\nhyper-vendor = { path = "../vendor" }\n')
        self.write('sdk/src/lib.rs', '/// SDK interface.\npub struct Thing;\n'
                   '/// External interface: [`hyper_vendor::Value`].\n'
                   'pub fn value() -> hyper_vendor::Value { hyper_vendor::Value }\n')
        self.write('vendor/Cargo.toml', '[package]\nname = "hyper-vendor"\n'
                   'version = "0.1.0"\nedition = "2024"\n')
        self.write('vendor/src/lib.rs', '#![doc(html_root_url = "https://example.org/vendor/")]\n'
                   '/// Third-party type.\npub struct Value;\n')
        packages = workspace_packages(application) + workspace_packages(sdk)
        target = self.root / 'build'
        environment = os.environ | {
            'CARGO_TARGET_DIR': str(target), 'RUSTC_BOOTSTRAP': '1',
            'RUSTDOCFLAGS': reference_doc_flags(documented_crates(packages), '..'),
        }
        command = ['cargo', 'doc', '--manifest-path', str(application), '--offline', '--no-deps']
        # Build the consumer first to exercise links before the SDK HTML exists.
        subprocess.run(command + ['--package', 'doc-app'], env=environment,
                       check=True, capture_output=True)
        self.assertFalse((target / 'doc/local_sdk/index.html').exists())
        subprocess.run(
            command
            + [arg for package in packages for arg in ('--package', package['name'])],
            env=environment, check=True, capture_output=True)
        documentation = target / 'doc'
        self.assertEqual({path.name for path in documentation.iterdir()
                          if (path / 'index.html').is_file()}, {'doc_app', 'local_sdk'})
        self.assertFalse((documentation / 'src/hyper_vendor').exists())
        page = documentation / 'doc_app/fn.make.html'
        links = [url for url in References(page.read_text()).links
                 if url.endswith('/struct.Thing.html')]
        self.assertTrue(links)
        for url in links:
            self.assertEqual((page.parent / url).resolve(),
                             documentation / 'local_sdk/struct.Thing.html')
        external_links = References((documentation / 'local_sdk/fn.value.html').read_text()).links
        self.assertIn('https://example.org/vendor/hyper_vendor/struct.Value.html', external_links)

    def test_portable_atomics_link_to_upstream_standard_library_docs(self):
        atomic = Path(__file__).resolve().parents[2] / 'kernel/src/sync/atomic.rs'
        manifest = self.write('fixture/Cargo.toml', '[package]\nname = "atomic-doc-probe"\n'
                              'version = "0.1.0"\nedition = "2024"\n[workspace]\n')
        self.write('fixture/src/lib.rs', f'#![no_std]\n#[path = {json.dumps(str(atomic))}]\n'
                   'pub mod atomic;\n')
        target = self.root / 'build'
        subprocess.run(['cargo', 'doc', '--manifest-path', str(manifest), '--offline', '--no-deps'],
                       env=os.environ | {'CARGO_TARGET_DIR': str(target), 'RUSTDOCFLAGS': DOC_FLAGS},
                       check=True, capture_output=True)
        docs = target / 'doc/atomic_doc_probe/atomic'
        links = References((docs / 'index.html').read_text()).links
        for item in ('enum.Ordering.html', 'type.AtomicBool.html', 'fn.fence.html'):
            self.assertTrue(any(url.startswith('https://doc.rust-lang.org/')
                                and url.endswith('/core/sync/atomic/' + item) for url in links))
            self.assertFalse((docs / item).exists())
        self.assertTrue((docs / 'struct.AtomicFlag.html').is_file())

    def test_inherited_rustdoc_link_is_fixed_without_rewriting_source_text(self):
        broken = 'Ord#lexicographical-comparison'
        untouched = (f'<pre>&lt;a href="{broken}"&gt;example&lt;/a&gt;</pre>\n'
                     f'<script>const example = \'<a href="{broken}">\';</script>\n'
                     f'<!-- <a href="{broken}"> -->\n')
        original = (untouched + f'<p>π\u2028 <a title="x &amp; y" href="{broken}">order</a> '
                    '<a href="struct.Local.html#method.cmp">local</a></p>')
        updated = fix_rustdoc_links(original)
        self.assertTrue(updated.startswith(untouched))
        self.assertIn('title="x &amp; y"', updated)
        self.assertEqual(References(updated).links, {
            'https://doc.rust-lang.org/core/cmp/trait.Ord.html#lexicographical-comparison',
            'struct.Local.html#method.cmp',
        })
        self.assertEqual(fix_rustdoc_links(updated), updated)

    def test_cross_crate_source_links_use_the_actual_page_depth(self):
        source = self.write('view/src/dependency/lib.rs.html', 'source')
        for item in ('crate/index.html', 'crate/module/item.html', 'crate/deep/module/item.html'):
            page = self.write('view/' + item, '')
            broken = '../src/dependency/lib.rs.html#39-41'
            html = f'<a class="src rightside" href="{broken}">Source</a>'
            fixed = fix_rustdoc_links(html, page, self.root / 'view')
            links = References(fixed).links
            self.assertEqual(len(links), 1)
            path, fragment = next(iter(links)).split('#')
            self.assertEqual((page.parent / path).resolve(), source)
            self.assertEqual(fragment, '39-41')
            self.assertEqual(fix_rustdoc_links(fixed, page, self.root / 'view'), fixed)
            if item == 'crate/index.html':
                self.assertEqual(fixed, html)
        for html in ('<a href="../src/dependency/lib.rs.html">ordinary link</a>',
                     '<a class="src" href="../src/missing.rs.html">Source</a>'):
            self.assertEqual(fix_rustdoc_links(html, page, self.root / 'view'), html)

    def test_root_url_and_symlink_are_rejected(self):
        self.write('index.html', '<a href="/api/">Wrong base path</a>')
        (self.root / 'linked.html').symlink_to(self.root / 'index.html')
        with self.assertRaises(ValueError) as failure:
            check_site(self.root)
        self.assertIn('symlinks', str(failure.exception))
        self.assertIn('Pages prefix', str(failure.exception))

    def test_pages_size_limit_is_checked_before_upload(self):
        self.write('index.html', '<p>Documentation</p>')
        with self.assertRaisesRegex(ValueError, 'artifact exceeds'):
            check_site(self.root, max_bytes=1)


if __name__ == '__main__':
    unittest.main()
