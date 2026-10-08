# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Behavioral fixtures for source links and the final Pages artifact."""

import importlib.util
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
from mkdocs.structure.files import File, Files
from pymdownx.superfences import fence_div_format
from types import SimpleNamespace
from xml.etree import ElementTree

# `site` is also the Python interpreter's startup module.
spec = importlib.util.spec_from_file_location('documentation_site', Path(__file__).with_name('site.py'))
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


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

    def test_links_into_rustdoc_are_checked_without_scanning_its_own_links(self):
        self.write('index.html', '<a href="api/rust/item.html#impl-T%3CU%3E">Type</a>'
                   '<a href="api/rust/item.html#12-15">Source</a>')
        self.write('api/rust/item.html', '<meta name="generator" content="rustdoc">'
                   '<div id="impl-T%3CU%3E"></div><a id="12"></a><a id="15"></a>'
                   '<a href="optional-runtime-target">Generated runtime link</a>')
        check_site(self.root, rustdoc_roots=('api/rust',))
        self.write('index.html', '<a href="api/rust/item.html#missing">Broken anchor</a>')
        with self.assertRaisesRegex(ValueError, 'missing anchor'):
            check_site(self.root, rustdoc_roots=('api/rust',))
        self.write('index.html', '<a href="api/rust/missing.html">Missing API page</a>')
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
