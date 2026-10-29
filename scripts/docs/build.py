#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Build one repository's documentation without checking out the system."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

from check_links import check_site


def build(root, output, repository, revision=None):
    root, output = root.resolve(), output.resolve()
    if output == root or root.is_relative_to(output):
        raise ValueError('documentation output must not contain the repository')
    if output.exists():
        raise ValueError(f'output already exists: {output}')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root, text=True).split('\0')
    files = sorted(name for name in tracked if name and (root / name).is_file()
                   and not any(part.startswith('.') for part in Path(name).parts)
                   and not name.startswith(('legacy/', 'third_party/')))
    pages = [name for name in files if name.endswith('.md')]
    metadata = root / 'component.json'
    if metadata.is_file():
        selected = json.loads(metadata.read_text()).get('documentation', {}).get('pages')
        if selected is not None:
            if (not isinstance(selected, list) or any(not isinstance(name, str) for name in selected)
                    or len(selected) != len(set(selected))
                    or any(name not in pages for name in selected)):
                raise ValueError('documentation.pages must select unique tracked Markdown files')
            pages = selected
    if 'README.md' not in pages:
        raise ValueError('the documentation entry point must be README.md')
    revision = revision or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    source = output / 'source'
    source.mkdir(parents=True)
    mapping = {name: ('index.md' if name == 'README.md' else name) for name in pages}
    assets = [name for name in files if Path(name).suffix.lower() in ('.png', '.svg', '.jpg', '.jpeg', '.gif', '.webp')]
    for name in pages + assets:
        destination = source / mapping.get(name, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, destination)
    tools = Path(__file__).resolve().parent
    (source / '_docs_assets').mkdir()
    shutil.copyfile(tools / 'mermaid.js', source / '_docs_assets/mermaid.js')
    settings = {'repository': repository, 'revision': revision, 'files': files,
                'pages': mapping, 'assets': assets}
    (output / 'sources.json').write_text(json.dumps(settings))
    config = {
        'site_name': repository + ' documentation',
        'site_url': f'https://hyper-proj.github.io/{repository}/',
        'repo_url': f'https://github.com/HypeR-Proj/{repository}',
        'edit_uri': '',  # Pages are staged and README files are renamed.
        'docs_dir': str(source), 'site_dir': str(output / 'site'),
        'use_directory_urls': False, 'strict': True,
        'theme': {'name': 'readthedocs'}, 'plugins': ['search'],
        'hooks': [str(tools / 'repository_site.py')],
        'markdown_extensions': ['tables', 'fenced_code', 'toc', 'pymdownx.tasklist'],
        'extra_javascript': ['https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js', '_docs_assets/mermaid.js'],
        'validation': {'links': {'not_found': 'warn', 'anchors': 'warn', 'unrecognized_links': 'warn'}},
        'extra': {'source_inventory': str(output / 'sources.json')},
    }
    path = output / 'mkdocs.yml'
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    subprocess.run([sys.executable, '-m', 'mkdocs', 'build', '--strict', '-f', str(path)], check=True)
    check_site(output / 'site', base_path=f'/{repository}/')
    return output / 'site'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    args = parser.parse_args()
    print(build(args.root, args.output, args.repository))


if __name__ == '__main__':
    main()
