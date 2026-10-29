# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Keep documentation links local and map source links to the owning repository."""

import json
import posixpath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from mkdocs.exceptions import PluginError

SETTINGS = None
CURRENT = None


def resolve(url, current, settings):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith('/'):
        return url
    inverse = {destination: source for source, destination in settings['pages'].items()}
    original = inverse[current]
    target = posixpath.normpath(posixpath.join(posixpath.dirname(original), unquote(parsed.path)))
    if target.startswith('../'):
        raise PluginError(f'{original}: cross-repository link must name its repository: {url}')
    if target == '.':
        target = 'README.md'
    if target + '/README.md' in settings['pages']:
        target += '/README.md'
    if target in settings['pages'] or target in settings['assets']:
        destination = settings['pages'].get(target, target)
        path = posixpath.relpath(destination, posixpath.dirname(current) or '.')
        return urlunsplit(('', '', path, parsed.query, parsed.fragment))
    if target in settings['files']:
        kind = 'blob'
    elif any(name.startswith(target + '/') for name in settings['files']):
        kind = 'tree'
    else:
        raise PluginError(f'{original}: missing link target: {url}')
    path = f"/HypeR-Proj/{settings['repository']}/{kind}/{settings['revision']}/{quote(target, safe='/')}"
    return urlunsplit(('https', 'github.com', path, parsed.query, parsed.fragment))


class Links(Treeprocessor):
    def run(self, element):
        for node in element.iter():
            attribute = {'a': 'href', 'img': 'src'}.get(node.tag)
            if attribute and node.get(attribute):
                node.set(attribute, resolve(node.get(attribute), CURRENT, SETTINGS))


class RepositoryLinks(Extension):
    def extendMarkdown(self, md):
        md.treeprocessors.register(Links(md), 'repository-links', 15)


def on_config(config):
    global SETTINGS
    with open(config.extra['source_inventory']) as stream:
        SETTINGS = json.load(stream)
    config.markdown_extensions.append(RepositoryLinks())
    return config


def on_page_markdown(markdown, page, **kwargs):
    global CURRENT
    CURRENT = page.file.src_uri
    return markdown
