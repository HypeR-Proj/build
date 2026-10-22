# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Organize published guides by purpose while preserving source ownership."""

from pathlib import PurePosixPath

SOURCE_OWNERS = {
    'app': 'Applications', 'bootstrap': 'Bootstrap', 'kernel': 'Kernel',
    'lib': 'Userspace libraries', 'mk': 'Build system', 'sdk': 'SDK',
    'stdlib': 'Native substrate', 'svc': 'Services', 'tests': 'Tests',
}


def page_title(path):
    for line in path.read_text().splitlines():
        if line.startswith('# '):
            return line[2:].strip().replace('`', '')
    return path.stem


def page_tree(root, paths, prefix):
    """Derive nested menus from directories; README is the local overview."""
    entries = []
    directories = {}
    for name in sorted(paths, key=lambda name: (PurePosixPath(name).name != 'README.md', name)):
        relative = PurePosixPath(name).relative_to(prefix)
        if len(relative.parts) == 1:
            entries.append({page_title(root / name): name})
        else:
            directories.setdefault(relative.parts[0], []).append(name)
    for directory, children in sorted(directories.items()):
        title = directory.replace('-', ' ').capitalize()
        if prefix == '.':
            title = SOURCE_OWNERS.get(directory, title)
        entries.append({title: page_tree(root, children, f'{prefix}/{directory}')})
    return entries


def navigation(root, pages):
    """Every guide has one navigation owner; unclassified guide folders fail."""
    remaining = set(pages)

    def take(prefix):
        selected = sorted(name for name in remaining if name.startswith(prefix + '/'))
        remaining.difference_update(selected)
        return page_tree(root, selected, prefix)

    nav = []
    for title, name in [('Home', 'README.md'), ('Documentation', 'docs/README.md')]:
        if name in remaining:
            remaining.remove(name)
            nav.append({title: name})
    for title, category in [('Learn', 'learn'), ('Architecture design', 'architecture'),
                            ('Detailed design', 'design'), ('Usage', 'usage')]:
        entries = take(f'docs/{category}')
        kernel = take(f'kernel/docs/{category}')
        if kernel:
            entries.append({'Kernel': kernel})
        if entries:
            nav.append({title: entries})
    nav.append({'API reference': 'api/index.md'})
    project = take('docs/project')
    for name in ('CONTRIBUTING.md', 'SECURITY.md'):
        if name in remaining:
            remaining.remove(name)
            project.append({page_title(root / name): name})
    if project:
        nav.append({'Project': project})
    misplaced = sorted(name for name in remaining if name.startswith(('docs/', 'kernel/docs/')))
    if misplaced:
        raise ValueError('guides need a purpose directory: ' + ', '.join(misplaced))
    if remaining:
        nav.append({'Source references': page_tree(root, remaining, '.')})
    return nav
