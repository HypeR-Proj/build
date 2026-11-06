# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Generate an output-owned execution view without editing source repositories."""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import stat
import tomllib
import xml.etree.ElementTree as ET

from . import files
from .model import NAME, dependency_order, discover, manifest_path, read_manifest, relative
from .composition import cargo_manifests, product_schema, source_targets, toml_document
from .editor import native_configs, publish_settings

COPY_SUFFIXES = {'.py', '.sh', '.mk', '.toml', '.lock', '.json', '.yml', '.yaml', '.config'}
STATE_VERSION = 5


def collect(modules, output):
    inputs = {}
    for name, module in sorted(modules.items()):
        for path in module.files(output):
            source = module.source / path
            info = source.stat()
            key = str(module.path / path)
            if key in inputs:
                raise ValueError(f'modules publish the same source path: {key}')
            inputs[key] = {'source': str(source), 'module': name, 'size': info.st_size,
                           'mtime': info.st_mtime_ns, 'mode': stat.S_IMODE(info.st_mode)}
    return inputs


def copied(path):
    # Scripts derive their workspace from __file__, and Cargo owns the generated
    # manifests/locks. Source bodies remain links; tools must not publish into
    # or accidentally resolve their execution root back into source checkouts.
    return path.suffix in COPY_SUFFIXES or path.name.endswith('_defconfig') or path.name in ('Makefile', 'Kconfig', 'hyper-cargo',
                                                        'hyper-clang', 'hyper-cc', 'hyper-clang++')


def signature(root, modules, inputs, configurations):
    data = {'root': str(root),
            'modules': {name: {'path': str(m.path), 'source': str(m.source), 'metadata': m.metadata}
                        for name, m in modules.items()},
            'manifest': [ET.tostring(project, encoding='unicode')
                         for project in read_manifest(manifest_path(root))],
            'files': inputs, 'configurations': configurations, 'version': STATE_VERSION}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def generate(root, output, modules, inputs, arch):
    view = output / 'workspace' / arch
    planned = {}
    sources = {}
    bodies = {}

    def read_source(path):
        key = str(path)
        if key not in bodies:
            bodies[key] = path.read_bytes()
            sources[key] = {'sha256': hashlib.sha256(bodies[key]).hexdigest(),
                            'mode': stat.S_IMODE(path.stat().st_mode)}
        return bodies[key]

    def regular(name, content, mode=0o644):
        relative(name)
        planned[str(name)] = ('file', content.encode() if isinstance(content, str) else content, mode)

    def link(name, source):
        relative(name)
        planned[str(name)] = ('link', source, None)

    for name, entry in sorted(inputs.items()):
        path, source = view / name, Path(entry['source'])
        # Frontends belong to source checkouts. Adapters retain their own build
        # recipes inside the execution view to avoid recursive preparation.
        owner = modules[entry['module']]
        if path == view / owner.path / 'Makefile' and owner.build.get('makefile'):
            source = owner.source / relative(owner.build['makefile'])
        if copied(path):
            regular(name, read_source(source), entry['mode'])
        else:
            link(name, source)

    # During migration old manifests may still carry links. They are projected
    # only into OUT, never materialized into the user's source tree by prepare.
    for project in read_manifest(manifest_path(root)):
        module = modules[project.attrib['name']]
        for item in project:
            if item.tag not in ('linkfile', 'copyfile'):
                continue
            source = str(module.path / relative(item.attrib['src']))
            destination = relative(item.attrib['dest'])
            if source in planned:
                kind, value, mode = planned[source]
                if kind == 'link':
                    regular(destination, read_source(value), stat.S_IMODE(value.stat().st_mode))
                else:
                    regular(destination, value, mode)
            elif any(name.startswith(source + '/') for name in planned):
                # The source workspace receives repo's directory link. The
                # output view owns its copies, including generated editor paths.
                for name in [name for name in planned if name.startswith(source + '/')]:
                    kind, value, mode = planned[name]
                    target = destination / Path(name).relative_to(source)
                    if kind == 'link':
                        link(target, value)
                    else:
                        regular(target, value, mode)
            else:
                raise ValueError(f'missing workspace input: {source}')

    for module in modules.values():
        for source_name, destination in module.metadata.get('workspace', {}).get('files', {}).items():
            source = module.source / relative(source_name)
            destination = relative(destination)
            if source.is_dir():
                owned = [module.source / path for path in module.files(output)
                         if (module.source / path).is_relative_to(source)]
            else:
                owned = [source]
            for body in owned:
                target = destination / body.relative_to(source) if source.is_dir() else destination
                if str(target) in planned:
                    if source_name == str(destination) and module.path == Path('.'):
                        continue
                    raise ValueError(f'{module.name}: workspace overlay conflicts with {target}')
                regular(target, read_source(body), stat.S_IMODE(body.stat().st_mode))

    policy = modules['build'].metadata.get('workspace', {})
    for name, content in cargo_manifests(modules).items():
        regular(name, content)
    # Resolve dependency identities in OUT, while Cargo and the language server
    # both name the original Rust source files instead of duplicate symlink paths.
    included_sources = {Path(item['source']) for item in inputs.values()}
    for name, item in inputs.items():
        if Path(name).name != 'Cargo.toml':
            continue
        data = tomllib.loads(planned[name][1].decode())
        if 'package' in data:
            regular(name, toml_document(source_targets(data, Path(item['source']).parent, included_sources)))
    native_editor = native_configs(modules, inputs, output, arch)
    for name, content in native_editor.items():
        regular(name, content)
    if policy.get('product_policy'):
        source = modules['build'].source / relative(policy['product_policy'])
        content = product_schema(modules, read_source(source).decode())
        regular('Kconfig', content)
        link('.config', output / 'config' / arch / '.config')

    editor_policy = policy.get('editor')
    if editor_policy or '.vscode/settings.json' in planned:
        body = (read_source(modules['build'].source / relative(editor_policy['template']))
                if editor_policy else planned['.vscode/settings.json'][1]).decode()
        settings = json.loads('\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('//')))
        settings['rust-analyzer.linkedProjects'] = sorted({
            str(module.path / 'Cargo.toml') for module in modules.values()
            if (module.source / 'Cargo.toml').is_file()} | {
                str(Path(name).parent.parent / 'Cargo.toml') for name in native_editor})
        settings['rust-analyzer.cargo.sysrootSrc'] = str(output / 'sdk' / arch / 'share/hyper/rust-src/library')
        settings['rust-analyzer.cargo.extraEnv'].update({
            'ARCH': arch, 'OUT': str(output), 'HYPER_BUILD_OUTPUT': str(output),
            'HYPER_KERNEL_SOURCE': str(modules['kernel'].source)})
        regular('.vscode/settings.json', json.dumps(settings, indent=4) + '\n')
        configuration = output / 'config' / arch / 'modules/kernel.config'
        regular('.vscode/rust-analyzer.toml', '[env]\nHYPER_CONFIG = ' + json.dumps(str(configuration)) + '\n')

    # Every backend's default output stays in OUT, including Cargo's defaults
    # when a nested build tool launches another command without --target-dir.
    for module in modules.values():
        directory = view / module.path
        destination = output / 'build' / module.name
        destination.mkdir(parents=True, exist_ok=True)
        link(module.path / 'target', destination)
    link('target', output / 'build' / 'build')

    manifest = manifest_path(root)
    regular(modules['manifest'].path / 'default.xml', read_source(manifest))
    provenance = {name: {'path': str(m.path), 'source': str(m.source)} for name, m in modules.items()}
    regular('modules.json', json.dumps(provenance, indent=2) + '\n')
    editor = {'folders': [{'name': 'HypeR', 'path': str(view)}]}
    regular('HypeR.code-workspace', json.dumps(editor, indent=2) + '\n')

    # Validate all declarations before replacing controls in an existing view.
    # The exclusive preparation lock keeps consumers out until publication ends.
    for name, (kind, value, mode) in sorted(planned.items()):
        if kind == 'file':
            files.write(view / name, value, mode)
        else:
            files.link(view / name, value)
    generated = set(planned)
    files.link(output / f'HypeR-{arch}.code-workspace', view / 'HypeR.code-workspace')

    previous_path = output / 'state' / arch / 'prepare.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_bytes())
        for name in set(previous.get('generated', [])) - generated:
            path = view / relative(name)
            if path.is_symlink() or path.is_file():
                path.unlink()
    controls = {name: {'sha256': hashlib.sha256(value).hexdigest(), 'mode': mode}
                for name, (kind, value, mode) in planned.items() if kind == 'file'}
    links = {name: os.path.relpath(value, (view / name).parent)
             for name, (kind, value, _) in planned.items() if kind == 'link'}
    return view, generated, controls, links, sources


def directory_stamps(paths):
    result = {}
    for path in paths:
        info = os.stat(path)
        result[path] = [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]
    return result


def matching_files(root, identities, *, follow_symlinks=False):
    try:
        for name, identity in identities.items():
            path = os.path.join(root, name)
            info = os.stat(path, follow_symlinks=follow_symlinks)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != identity['mode']:
                return False
            with open(path, 'rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != identity['sha256']:
                    return False
    except OSError:
        return False
    return True


def launch_inputs_intact(state):
    try:
        return (directory_stamps(state['source_directories']) == state['source_directories']
                and matching_files('', state['control_sources'], follow_symlinks=True))
    except OSError:
        return False


def intact(view, state, *, launch=False):
    editor = view.parent.parent / f'HypeR-{view.name}.code-workspace'
    try:
        return (os.readlink(editor) == os.path.relpath(view / 'HypeR.code-workspace', editor.parent)
                and matching_files(view, state['controls'])
                and all(os.path.exists(path := os.path.join(view, name)) and os.readlink(path) == target
                        for name, target in state['launch_links' if launch else 'links'].items()))
    except OSError:
        return False


def hashes(directory, root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()}


def cached(root, output, arch, *, launch=False):
    """Read-only fast path lets independent builds share an unchanged view."""
    state_path = output / 'state' / arch / 'prepare.json'
    marker = output / 'workspace-owner.json'
    if not state_path.exists() or not marker.exists():
        return None
    if json.loads(marker.read_bytes()) != {'workspace': str(root), 'version': 1}:
        return None
    state = json.loads(state_path.read_bytes())
    if state.get('version') != STATE_VERSION:
        return None
    modules = discover(root)
    if launch:
        # Launch existing images without inspecting compiler inputs or running
        # Git once per module. Controls still use content validation, and a
        # changed file inventory or configuration falls back to full prepare.
        if state['module_identity'] != signature(root, modules, {}, {}) or not launch_inputs_intact(state):
            return None
    else:
        if (state.get('source_identity') != signature(root, modules, collect(modules, output), {})
                or not matching_files('', state['control_sources'], follow_symlinks=True)):
            return None
    if any(not (output / path).is_file()
           or hashlib.sha256((output / path).read_bytes()).hexdigest() != identity
           for path, identity in state.get('configuration_files', {}).items()):
        return None
    view = output / 'workspace' / arch
    if not intact(view, state, launch=launch):
        return None
    return view, modules


def prepare(root, output, arch='aarch64'):
    root, output = root.resolve(), output.resolve()
    if output == root or root.is_relative_to(output):
        raise ValueError('OUT must not contain the source workspace')
    if not NAME.fullmatch(arch):
        raise ValueError('invalid architecture name')
    with files.lock(output):
        modules = discover(root)
        dependency_order(modules, modules)
        marker = output / 'workspace-owner.json'
        owner = {'workspace': str(root), 'version': 1}
        if marker.exists() and json.loads(marker.read_bytes()) != owner:
            raise ValueError('OUT belongs to another workspace; choose a separate output directory')
        files.write(marker, json.dumps(owner, indent=2) + '\n')
        from .config import prepare_configs, prepare_product
        configurations = prepare_configs(root, output, modules, arch)
        policy = modules['build'].metadata.get('workspace', {})
        if policy.get('product_policy'):
            source = modules['build'].source / relative(policy['product_policy'])
            schema = product_schema(modules, source.read_text())
            product = prepare_product(root, output, arch, schema)
            configurations['$product'] = hashlib.sha256(product.read_bytes()).hexdigest()
        # Snapshot before inventory/copying, so an addition during generation
        # cannot be mistaken for a helper already present in the prepared view.
        # Empty directories matter too; output and Git internals are excluded.
        directories = directory_stamps({str(base) for module in modules.values()
                                        for base, _ in module.walk(output)})
        inputs = collect(modules, output)
        identity = signature(root, modules, inputs, configurations)
        state_path = output / 'state' / arch / 'prepare.json'
        if state_path.exists():
            state = json.loads(state_path.read_bytes())
            if (state.get('version') == STATE_VERSION and state.get('identity') == identity
                    and launch_inputs_intact(state) and intact(output / 'workspace' / arch, state)):
                publish_settings(output / 'workspace' / arch, modules)
                return output / 'workspace' / arch, modules, False, identity
        view, generated, controls, links, sources = generate(root, output, modules, inputs, arch)
        files.write(state_path, json.dumps({'version': STATE_VERSION, 'identity': identity,
                                           'source_identity': signature(root, modules, inputs, {}),
                                           'module_identity': signature(root, modules, {}, {}),
                                           'control_sources': sources, 'source_directories': directories,
                                           'configuration_files': hashes(output / 'config' / arch, output),
                                           'generated': sorted(generated), 'controls': controls,
                                           'links': links,
                                           # Output/configuration links are used by launchers;
                                           # source-body links are checked by normal builds.
                                           'launch_links': {name: target for name, target in links.items()
                                                            if name not in inputs}}, indent=2) + '\n')
        publish_settings(view, modules)
        return view, modules, True, identity


@contextmanager
def execution(root, output, arch='aarch64', *, exclusive=False, launch=False):
    """Hold a complete prepared view for all commands in one invocation."""
    root, output = root.resolve(), output.resolve()
    if output == root or root.is_relative_to(output):
        raise ValueError('OUT must not contain the source workspace')
    if not NAME.fullmatch(arch):
        raise ValueError('invalid architecture name')
    changed = False
    if not exclusive:
        with files.lock(output, shared=True):
            if ready := cached(root, output, arch, launch=launch):
                yield ready[0], ready[1], False
                return
    while True:
        view, modules, updated, identity = prepare(root, output, arch)
        changed |= updated
        with files.lock(output, shared=not exclusive):
            state = json.loads((output / 'state' / arch / 'prepare.json').read_bytes())
            if state['identity'] != identity:
                # Another prepare won the interval between locks. Never build
                # a view using dependency metadata from an older generation.
                continue
            yield view, modules, changed
            return
