# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Repair unresolved upstream and cross-crate source links emitted by Rustdoc."""

from html import escape
from html.parser import HTMLParser
import os
from urllib.parse import quote, unquote, urlsplit, urlunsplit

# Rustdoc 1.97 leaves this shorthand unresolved in inherited Iterator::cmp
# descriptions. The standard-library trait remains the owner of that section.
UPSTREAM_LINKS = {
    'Ord#lexicographical-comparison':
        'https://doc.rust-lang.org/core/cmp/trait.Ord.html#lexicographical-comparison',
}


class RustdocLinks(HTMLParser):
    def __init__(self, text, page=None, root=None):
        super().__init__(convert_charrefs=True)
        self.page = page.resolve() if page is not None else None
        self.root = root.resolve() if root is not None else None
        self.offsets = [0]
        for line in text.split('\n')[:-1]:
            self.offsets.append(self.offsets[-1] + len(line) + 1)
        self.replacements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        href = values.get('href')
        if tag != 'a' or not href:
            return
        target = UPSTREAM_LINKS.get(href, href)
        if 'src' in values.get('class', '').split():
            target = self.source_link(target)
        if target == href:
            return
        attributes = []
        for name, value in attrs:
            if name == 'href':
                value = target
            attributes.append(name if value is None else f'{name}="{escape(value, quote=True)}"')
        line, column = self.getpos()
        start = self.offsets[line - 1] + column
        self.replacements.append((start, len(self.get_starttag_text()),
                                  '<a ' + ' '.join(attributes) + '>'))

    def source_link(self, href):
        # Cross-crate source locations can retain a crate-index-relative
        # ../src/ URL even on nested item pages. Repair only a missing Source
        # target whose exact file exists in this Rustdoc view's source tree.
        if self.page is None or self.root is None:
            return href
        url = urlsplit(href)
        path = unquote(url.path)
        if url.scheme or url.netloc or not path.startswith('../src/'):
            return href
        if (self.page.parent / path).is_file():
            return href
        source = (self.root / path[3:]).resolve()
        if not source.is_relative_to(self.root / 'src') or not source.is_file():
            return href
        relative = os.path.relpath(source, self.page.parent).replace(os.sep, '/')
        return urlunsplit(('', '', quote(relative, safe='/'), url.query, url.fragment))


def fix_rustdoc_links(text, page=None, root=None):
    # Only actual anchor attributes are rewritten;
    # prose, escaped source examples, scripts and unrelated URLs retain bytes.
    if not any(link in text for link in UPSTREAM_LINKS) and '../src/' not in text:
        return text
    for start, length, replacement in reversed(RustdocLinks(text, page, root).replacements):
        text = text[:start] + replacement + text[start + length:]
    return text
