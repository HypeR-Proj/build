# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""MkDocs hooks: preserve repository links while checking their real targets."""

import json
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File

ROOT = Path(__file__).resolve().parents[2]
CURRENT_PAGE = None
SETTINGS = None


def source_link(url, page, root, sources, revision, staged):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return url
    path = unquote(parsed.path)
    target = (root / page).parent.joinpath(path).resolve()
    if not target.is_relative_to(root):
        raise PluginError(f'{page}: link escapes repository: {url}')
    relative = target.relative_to(root).as_posix()
    # Staged guides, media and generated API pages stay inside the website.
    if (staged / relative).is_file():
        return url
    if relative in sources and target.is_file():
        kind = 'blob'
    elif target.is_dir() and any(name.startswith(relative + '/') for name in sources):
        kind = 'tree'
    else:
        raise PluginError(f'{page}: missing or unpublished link target: {url}')
    return urlunsplit(('https', 'github.com',
                      f'/roolrz/HypeR/{kind}/{revision}/{quote(relative, safe="/")}',
                      parsed.query, parsed.fragment))


class RepositoryLinks(Treeprocessor):
    def run(self, element):
        for node in element.iter():
            attribute = {'a': 'href', 'img': 'src'}.get(node.tag)
            if attribute and attribute in node.attrib:
                node.set(attribute, source_link(
                    node.get(attribute), CURRENT_PAGE.file.src_uri, ROOT,
                    SETTINGS['sources'], SETTINGS['revision'],
                    Path(CURRENT_PAGE.file.src_dir)))


class RepositoryLinkExtension(Extension):
    def extendMarkdown(self, md):
        # After Markdown has parsed inline/reference links, before MkDocs
        # resolves page URLs. Code samples and ordinary prose are untouched.
        md.treeprocessors.register(RepositoryLinks(md), 'repository-links', 15)


class ApiAsset(File):
    def is_documentation_page(self):
        # Rustdoc ships Markdown font licenses as assets. Preserve their bytes
        # and URLs instead of treating them as project guides.
        return False


def on_files(files, config):
    for file in list(files):
        if (file.src_uri.startswith('api/') and file.src_uri != 'api/index.md'
                and file.is_documentation_page()):
            files.remove(file)
            files.append(ApiAsset(file.src_uri, file.src_dir, config.site_dir, False))
    return files


def on_config(config):
    global SETTINGS
    SETTINGS = json.loads((Path(config.docs_dir).parent / 'manifest.json').read_text())
    config.markdown_extensions.append(RepositoryLinkExtension())
    config.nav = SETTINGS['nav']
    config.copyright = f"Source revision: {SETTINGS['revision'][:12]}"
    return config


def on_page_markdown(markdown, page, **kwargs):
    global CURRENT_PAGE
    CURRENT_PAGE = page
    return markdown
