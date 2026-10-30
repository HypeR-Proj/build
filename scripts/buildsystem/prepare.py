# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Generate an output-owned execution view without editing source repositories."""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import stat

from . import files
from .model import NAME, dependency_order, discover, manifest_path, read_manifest, relative
from .composition import cargo_manifests, product_schema

COPY_SUFFIXES = {'.py', '.sh', '.mk', '.toml', '.lock', '.json', '.yml', '.yaml', '.config'}


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
    data = {'root': str(root), 'modules': {name: m.metadata for name, m in modules.items()},
            'files': inputs, 'configurations': configurations, 'version': 2}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def generate(root, output, modules, inputs, arch):
    view = output / 'workspace' / arch
    planned = {}

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
            regular(name, source.read_bytes(), entry['mode'])
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
                    regular(destination, value.read_bytes(), stat.S_IMODE(value.stat().st_mode))
                else:
                    regular(destination, value, mode)
            elif any(name.startswith(source + '/') for name in planned):
                link(destination, view / source)
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
                    raise ValueError(f'{module.name}: workspace overlay conflicts with {target}')
                regular(target, body.read_bytes(), stat.S_IMODE(body.stat().st_mode))

    policy = modules['build'].metadata.get('workspace', {})
    for name, content in cargo_manifests(modules, policy.get('cargo', {})).items():
        regular(name, content)
    if policy.get('product_policy'):
        source = modules['build'].source / relative(policy['product_policy'])
        content = product_schema(modules, source.read_text())
        regular('Kconfig', content)
        link('.config', output / 'config' / arch / '.config')

    if '.vscode/settings.json' in planned:
        body = planned['.vscode/settings.json'][1].decode()
        settings = json.loads('\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('//')))
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
    if 'manifest/default.xml' not in planned and '.workspace/default.xml' not in planned:
        regular('manifest/default.xml', manifest.read_bytes())
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
    return view, generated, controls, links


def intact(view, state):
    editor = view.parent.parent / f'HypeR-{view.name}.code-workspace'
    return (editor.is_symlink()
            and os.readlink(editor) == os.path.relpath(view / 'HypeR.code-workspace', editor.parent)
            and all((view / name).exists() for name in state['generated'])
            and all((view / name).is_file() and not (view / name).is_symlink()
                    and hashlib.sha256((view / name).read_bytes()).hexdigest() == identity['sha256']
                    and stat.S_IMODE((view / name).stat().st_mode) == identity['mode']
                    for name, identity in state.get('controls', {}).items())
            and all((view / name).is_symlink() and os.readlink(view / name) == target
                    for name, target in state.get('links', {}).items()))


def hashes(directory, root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()}


def cached(root, output, arch):
    """Read-only fast path lets independent builds share an unchanged view."""
    state_path = output / 'state' / arch / 'prepare.json'
    marker = output / 'workspace-owner.json'
    if not state_path.exists() or not marker.exists():
        return None
    if json.loads(marker.read_bytes()) != {'workspace': str(root), 'version': 1}:
        return None
    state = json.loads(state_path.read_bytes())
    if state.get('version') != 2:
        return None
    modules = discover(root)
    if state.get('source_identity') != signature(root, modules, collect(modules, output), {}):
        return None
    if any(not (output / path).is_file()
           or hashlib.sha256((output / path).read_bytes()).hexdigest() != identity
           for path, identity in state.get('configuration_files', {}).items()):
        return None
    view = output / 'workspace' / arch
    if not intact(view, state):
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
        inputs = collect(modules, output)
        identity = signature(root, modules, inputs, configurations)
        state_path = output / 'state' / arch / 'prepare.json'
        if state_path.exists():
            state = json.loads(state_path.read_bytes())
            if state.get('version') == 2 and state.get('identity') == identity and intact(output / 'workspace' / arch, state):
                return output / 'workspace' / arch, modules, False, identity
        view, generated, controls, links = generate(root, output, modules, inputs, arch)
        files.write(state_path, json.dumps({'version': 2, 'identity': identity,
                                           'source_identity': signature(root, modules, inputs, {}),
                                           'configuration_files': hashes(output / 'config' / arch, output),
                                           'generated': sorted(generated), 'controls': controls,
                                           'links': links}, indent=2) + '\n')
        return view, modules, True, identity


@contextmanager
def execution(root, output, arch='aarch64', *, exclusive=False):
    """Hold a complete prepared view for all commands in one invocation."""
    root, output = root.resolve(), output.resolve()
    if output == root or root.is_relative_to(output):
        raise ValueError('OUT must not contain the source workspace')
    if not NAME.fullmatch(arch):
        raise ValueError('invalid architecture name')
    changed = False
    if not exclusive:
        with files.lock(output, shared=True):
            if ready := cached(root, output, arch):
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
