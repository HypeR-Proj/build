# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Keep published guide URLs usable without retaining duplicate Markdown."""

from html import escape
import json
import os
from pathlib import PurePosixPath
from urllib.parse import quote


def html_path(source):
    path = PurePosixPath(source)
    if path.is_absolute() or '..' in path.parts or path.suffix != '.md':
        raise ValueError(f'invalid guide redirect path: {source}')
    if path.name in ('README.md', 'index.md'):
        return path.with_name('index.html')
    return path.with_suffix('') / 'index.html'


def write_redirects(site, manifest):
    for old, route in json.loads(manifest.read_text())['pages'].items():
        destination = site / html_path(old)
        # Moving foo.md to foo/README.md preserves its published directory URL.
        # The real guide already serves old bookmarks; no redirect may cover it.
        if (old != route['target'] and not route.get('anchors')
                and html_path(old) == html_path(route['target']) and destination.is_file()):
            continue
        if destination.exists():
            raise ValueError(f'redirect would overwrite a published page: {old}')

        def url_for(source):
            name, separator, fragment = source.partition('#')
            target = site / html_path(name)
            if not target.is_file():
                raise ValueError(f'redirect target is missing: {source}')
            return quote(os.path.relpath(target, destination.parent)) + separator + fragment

        url = url_for(route['target'])
        fragments = {name: url_for(target) for name, target in route.get('anchors', {}).items()}
        sections = ''.join(f'<li><a href="{escape(target, quote=True)}">'
                           f'{escape(name.replace("-", " "))}</a></li>'
                           for name, target in fragments.items())
        script = quote(os.path.relpath(site / 'assets/redirect.js', destination.parent))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="robots" content="noindex"><title>Documentation moved</title></head>'
            '<body><p>This guide has moved. '
            f'<a id="destination" href="{escape(url, quote=True)}" '
            f'data-anchors="{escape(json.dumps(fragments), quote=True)}">Open the current guide</a>.'
            f'</p>{"<ul>" + sections + "</ul>" if sections else ""}'
            f'<script src="{escape(script, quote=True)}"></script></body></html>\n')
