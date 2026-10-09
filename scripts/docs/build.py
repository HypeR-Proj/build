#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Build the same complete documentation artifact locally and in PR/deploy CI."""

import argparse
from html import escape
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from check_links import check_site
from rustdoc import fix_rustdoc_links

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'target/documentation'
TOOLS = ROOT / 'scripts/docs'
TARGETS = {
    'aarch64': 'aarch64-unknown-none',
    'riscv64': 'riscv64imac-unknown-none-elf',
    'x86_64': 'x86_64-unknown-none',
}
DOC_FLAGS = ('-D rustdoc::broken_intra_doc_links -D rustdoc::invalid_html_tags '
             '-D rustdoc::invalid_codeblock_attributes')


def run(command, *, cwd=ROOT, env=None):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(command, cwd=cwd, env=os.environ | (env or {}), check=True)


def clean(path):
    # All generated content stays under the dedicated documentation directory.
    if not path.resolve().is_relative_to(OUTPUT.resolve()) or path == OUTPUT:
        raise ValueError(f'refusing to remove {path}')
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def public_files():
    output = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    result = []
    for name in sorted(set(output.decode().split('\0')) - {''}):
        path = ROOT / name
        if (name.startswith(('.agent/', '.agents/', '.codex/'))
                or path.is_symlink() or not path.is_file()):
            continue
        result.append(name)
    return result


def rust_documentation(api):
    abi_packages = workspace_packages(ROOT / 'sdk/abi/Cargo.toml')
    kernel_packages = workspace_packages(ROOT / 'kernel/Cargo.toml') + abi_packages
    environment = {'RUSTDOCFLAGS': reference_doc_flags(documented_crates(kernel_packages), '..'),
                   'RUSTC_BOOTSTRAP': '1'}
    for architecture, target in TARGETS.items():
        cargo_output = OUTPUT / 'cargo/kernel'
        clean(cargo_output / target / 'doc')
        run(['make', '-C', 'kernel', 'doc', f'ARCH={architecture}',
             f'CONFIG_FILE=configs/qemu_{architecture}_defconfig'],
            env=environment | {'CARGO_TARGET_DIR': str(cargo_output)})
        copy_rustdoc(cargo_output / target / 'doc', api / 'kernel' / architecture)

    applications = workspace_packages(ROOT / 'app/Cargo.toml')
    sdk_packages = workspace_packages(ROOT / 'lib/rust/Cargo.toml') + abi_packages
    packages = [arg for package in applications + sdk_packages
                for arg in ('--package', package['name'])]
    environment['RUSTDOCFLAGS'] = reference_doc_flags(
        documented_crates(applications + sdk_packages), '..')
    programs = native_programs(applications)
    for architecture in ('aarch64', 'riscv64'):
        sdk = OUTPUT / 'sdk' / architecture
        run(['make', 'sdk', f'ARCH={architecture}', f'SDK_OUTPUT={sdk}'])
        cargo_output = OUTPUT / 'cargo/native' / architecture
        target = f'{architecture}-unknown-hyper'
        documentation = cargo_output / target / 'doc'
        clean(documentation)
        command = [str(sdk / 'bin/hyper-cargo'), 'doc', '--manifest-path', 'app/Cargo.toml',
                   '--no-deps', '--document-private-items', '--locked']
        # SDK crates are dependencies outside the application workspace. Select
        # them explicitly so --no-deps excludes only third-party documentation.
        run(command + packages,
            env=environment | {'CARGO_TARGET_DIR': str(cargo_output)})
        library_view = api / 'native' / architecture
        copy_rustdoc(documentation, library_view)

        # Default cargo doc skips same-name binaries. Save the library view
        # first, then explicitly select production binaries in a clean doc tree.
        # Their library links point two levels up from each crate's HTML pages.
        flags = program_doc_flags(library_view)
        clean(documentation)
        run(command + ['--workspace'] + [arg for name in programs for arg in ('--bin', name)],
            env={'RUSTDOCFLAGS': flags, 'CARGO_TARGET_DIR': str(cargo_output)})
        for name in programs:
            if not (documentation / name.replace('-', '_') / 'index.html').is_file():
                raise ValueError(f'missing executable documentation: {architecture}/{name}')
        copy_rustdoc(documentation, library_view / 'programs')


def workspace_packages(manifest):
    metadata = json.loads(subprocess.check_output(
        ['cargo', 'metadata', '--no-deps', '--offline', '--format-version', '1',
         '--manifest-path', str(manifest)], cwd=ROOT))
    return [package for package in metadata['packages']
            if package['id'] in metadata['workspace_members']]


def native_programs(packages):
    return sorted(target['name'] for package in packages
                  for target in package['targets']
                  if 'bin' in target['kind'] and target['doc']
                  and not target.get('required-features')
                  and Path(target['src_path']).is_relative_to(
                      Path(package['manifest_path']).parent / 'src'))


def documented_crates(packages):
    return sorted({target['name'].replace('-', '_') for package in packages
                   for target in package['targets']
                   if target['doc'] and not target.get('required-features')
                   and any(kind in ('lib', 'dylib', 'cdylib', 'bin', 'proc-macro')
                           for kind in target['kind'])})


def reference_doc_flags(crates, root_url):
    # --no-deps does not guarantee a selected dependency's HTML already exists
    # when its consumer is documented. Explicit roots make links order-independent.
    flags = [DOC_FLAGS, '-Z unstable-options', '--extern-html-root-takes-precedence']
    flags.extend(f'--extern-html-root-url {crate}={root_url}' for crate in crates)
    return ' '.join(flags)


def program_doc_flags(library_view):
    crates = (crate.name for crate in sorted(library_view.iterdir())
              if (crate / 'index.html').is_file())
    return reference_doc_flags(crates, '../..')


def copy_rustdoc(source, destination):
    shutil.copytree(source, destination)
    for page in destination.rglob('*.html'):
        original = page.read_text()
        updated = fix_rustdoc_links(original, page, destination)
        if updated != original:
            page.write_text(updated)
    # Cargo doc does not create a workspace landing page by default, but the
    # generated help/settings pages link back to one.
    crates = sorted(path.name for path in destination.iterdir()
                    if (path / 'index.html').is_file())
    links = ''.join(f'<li><a href="{escape(crate)}/index.html">{escape(crate)}</a></li>'
                    for crate in crates)
    home = Path(os.path.relpath(OUTPUT / 'api/index.html', destination)).as_posix()
    (destination / 'index.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>Rust API reference</title><h1>Rust API reference</h1>'
        f'<p><a href="{escape(home)}">All API references</a></p><ul>{links}</ul></html>')


def api_documentation(revision):
    api = OUTPUT / 'api'
    clean(api)
    rust_documentation(api)
    run(['doxygen', str(TOOLS / 'Doxyfile')], env={
        'HYPER_DOC_REVISION': revision,
        'HYPER_DOC_C_OUTPUT': str(api / 'c'),
    })
    lines = [
        '# API reference', '',
        'These pages are generated from the existing standard documentation comments.',
        'Rustdoc also lists item signatures and source locations, including private items.',
        'Undocumented functions receive no generated explanation. C entries require',
        'Doxygen comments; ordinary `/* ... */` comments are not converted.', '',
        'Each Rust view uses the named architecture and its normal build configuration.',
        'Only HypeR-owned crates are documented. Native views include applications,',
        'the installed SDK and ABI, built against the HypeR target and standard library.',
        'Third-party crates, the standard library and test-only features are excluded',
        'from the generated references.', '',
        'Executable references have separate output from same-name libraries; their',
        'type links return to the library view for the same architecture.', '',
        f'Source revision: `{revision}`.', '',
    ]
    for area, architectures in (('kernel', TARGETS), ('native', ('aarch64', 'riscv64'))):
        for architecture in architectures:
            lines.extend([f'## {area.title()} — {architecture}', ''])
            for crate in sorted((api / area / architecture).iterdir()):
                if crate.name != 'programs' and (crate / 'index.html').is_file():
                    link = f'{area}/{architecture}/{crate.name}/index.html'
                    lines.append(f'- [{crate.name}]({link})')
            lines.append('')
            programs = api / area / architecture / 'programs'
            if programs.is_dir():
                lines.extend(['### Executables', ''])
                for crate in sorted(programs.iterdir()):
                    if (crate / 'index.html').is_file():
                        link = f'{area}/{architecture}/programs/{crate.name}/index.html'
                        lines.append(f'- [{crate.name}]({link})')
                lines.append('')
    lines.extend(['## C SDK', '', '- [Documented C interfaces and source](c/index.html)', ''])
    (api / 'index.md').write_text('\n'.join(lines))


def page_title(path):
    for line in path.read_text().splitlines():
        if line.startswith('# '):
            return line[2:].strip().replace('`', '')
    return path.stem


def prepare_site(revision):
    source = OUTPUT / 'source'
    clean(source)
    api = OUTPUT / 'api'
    if not (api / 'index.md').is_file():
        raise ValueError('API output is missing; run the complete build first')
    shutil.copytree(api, source / 'api')
    files = public_files()
    groups = {name: [] for name in ('Guides', 'Kernel', 'SDK', 'Libraries', 'Applications', 'Tests', 'Project')}
    for name in files:
        path = Path(name)
        if path.suffix.lower() not in ('.md', '.png', '.svg', '.jpg', '.jpeg', '.gif'):
            continue
        # GitHub templates are repository metadata, not published guides.
        if name.startswith(('.', 'third_party/')):
            continue
        destination = source / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
        if path.suffix != '.md' or name == 'README.md':
            continue
        if name.startswith(('tests/', 'kernel/tests/')):
            group = 'Tests'
        else:
            group = {'docs': 'Guides', 'kernel': 'Kernel', 'sdk': 'SDK', 'lib': 'Libraries',
                     'app': 'Applications'}.get(path.parts[0], 'Project')
        groups[group].append({page_title(ROOT / path): name})
    assets = source / 'assets'
    assets.mkdir()
    shutil.copyfile(TOOLS / 'mermaid.js', assets / 'mermaid.js')
    nav = [{'Home': 'README.md'}, {'API reference': 'api/index.md'}]
    nav.extend({group: entries} for group, entries in groups.items() if entries)
    (OUTPUT / 'manifest.json').write_text(json.dumps({
        'revision': revision, 'sources': files, 'nav': nav,
    }, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-api', action='store_true',
                        help='local Markdown preview only: reuse an earlier complete API build')
    options = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if not options.reuse_api:
        api_documentation(revision)
    prepare_site(revision)
    run([sys.executable, '-m', 'mkdocs', 'build', '--strict', '-f', str(TOOLS / 'mkdocs.yml')])
    # Rustdoc checks source-level intra-doc links. Validate generated link
    # targets too, allowing its dynamic anchors and optional trait scripts.
    check_site(OUTPUT / 'site', rustdoc_roots=('api/kernel', 'api/native'))
    print(f'Documentation artifact: {OUTPUT / "site"}')


if __name__ == '__main__':
    main()
