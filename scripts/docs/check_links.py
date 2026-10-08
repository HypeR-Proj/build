#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate local HTML links and assets in the artifact that Pages will serve."""

from html.parser import HTMLParser
from functools import cache
from pathlib import Path
from urllib.parse import unquote, urlsplit
import sys


class References(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.anchors = set()
        self.links = set()
        self.generator = None
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get('id'):
            self.anchors.add(attributes['id'])
        if tag == 'a' and attributes.get('name'):
            self.anchors.add(attributes['name'])
        if tag == 'meta' and attributes.get('name') == 'generator':
            self.generator = attributes.get('content')
        for name in ('href', 'src'):
            if attributes.get(name):
                self.links.add(attributes[name])

    def has_anchor(self, fragment):
        if fragment in self.anchors or unquote(fragment) in self.anchors:
            return True
        # Rustdoc source views implement line-range fragments in JavaScript.
        start, separator, end = fragment.partition('-')
        return (self.generator == 'rustdoc' and separator and start.isdecimal()
                and end.isdecimal() and int(start) <= int(end)
                and start in self.anchors and end in self.anchors)


def check_site(site, base_path='/HypeR/', max_bytes=1_000_000_000, rustdoc_roots=()):
    site = site.resolve()
    errors = []
    pages = {}
    files = set()
    rustdoc_roots = tuple(site / root for root in rustdoc_roots)
    total_bytes = 0
    for path in site.rglob('*'):
        if path.is_symlink():
            errors.append(f'{path.relative_to(site)}: symlinks cannot enter the Pages artifact')
        elif path.is_file():
            files.add(path)
            total_bytes += path.stat().st_size
            if path.suffix == '.html' and not any(path.is_relative_to(root) for root in rustdoc_roots):
                pages[path] = References(path.read_text())
    if total_bytes > max_bytes:
        errors.append(f'Pages artifact exceeds {max_bytes} bytes: {total_bytes}')
    if site / 'index.html' not in pages:
        errors.append('missing site index.html')
    @cache
    def destination_for(parent, path):
        destination = (parent / path).resolve()
        if destination.is_dir():
            destination /= 'index.html'
        return destination

    @cache
    def references_for(path):
        return pages[path] if path in pages else References(path.read_text())

    for page, references in pages.items():
        for url in references.links:
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc:
                continue
            path = unquote(parsed.path)
            if path.startswith('/'):
                if not path.startswith(base_path):
                    errors.append(f'{page.relative_to(site)}: URL escapes project Pages prefix: {url}')
                    continue
                destination = destination_for(site, path[len(base_path):])
            else:
                destination = destination_for(page.parent, path) if path else page
            if not destination.is_relative_to(site):
                errors.append(f'{page.relative_to(site)}: URL escapes site: {url}')
                continue
            if destination not in files:
                errors.append(f'{page.relative_to(site)}: missing file: {url}')
                continue
            fragment = parsed.fragment
            if fragment and not fragment.startswith(':~:text=') and destination.suffix == '.html':
                if not references_for(destination).has_anchor(fragment):
                    errors.append(f'{page.relative_to(site)}: missing anchor: {url}')
    if errors:
        raise ValueError('\n'.join(errors))
    print(f'Validated links from {len(pages)} guide/C pages; artifact size {total_bytes:,} bytes')


if __name__ == '__main__':
    check_site(Path(sys.argv[1]))
