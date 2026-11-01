# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Generate installed-SDK discovery configuration for Native Cargo roots."""

from pathlib import Path
import tomllib

from .composition import toml_document


def native_configs(modules, inputs, output, arch):
    roots = {}
    for module in modules.values():
        entries = module.metadata.get('cargo', [])
        if not entries:
            continue
        roots[module.path] = ('static' if all(e['domain'] == 'boot' for e in entries) else 'dynamic', '1')
        for entry in entries:
            if entry.get('link_probe'):
                roots[module.path / Path(entry['link_probe']).parent] = ('dynamic', '1')
    # SDK and standalone test fixtures already declare their Native SDK patches.
    # Preserve their scope, but replace source-navigation paths with the installed
    # contract so Cargo metadata and editor checks resolve the same packages.
    for name, item in inputs.items():
        path = Path(name)
        if path.name != 'config.toml' or path.parent.name != '.cargo':
            continue
        data = tomllib.loads(Path(item['source']).read_text())
        if 'hyper-os' not in data.get('patch', {}).get('crates-io', {}):
            continue
        target = data.get('build', {}).get('target', '')
        runtime = '0' if target.endswith(('-none', '-none-elf')) else '1'
        mode = data.get('env', {}).get('HYPER_LINK_MODE', 'dynamic')
        roots.setdefault(path.parent.parent, (mode, runtime))
    sdk = output / 'sdk' / arch
    configs = {}
    for root, (mode, runtime) in roots.items():
        name = str(root / '.cargo/config.toml')
        data = tomllib.loads(Path(inputs[name]['source']).read_text()) if name in inputs else {}
        target = (str(sdk / 'share/hyper/targets' / f'{arch}-unknown-hyper.json') if runtime == '1' else
                  {'aarch64': 'aarch64-unknown-none', 'riscv64': 'riscv64gc-unknown-none-elf',
                   'x86_64': 'x86_64-unknown-none'}[arch])
        data.setdefault('build', {})['target'] = target
        data.setdefault('unstable', {})['json-target-spec'] = True
        data.setdefault('env', {}).update(HYPER_LINK_MODE=mode, HYPER_RUST_STD=runtime)
        patches = data.setdefault('patch', {}).setdefault('crates-io', {})
        for package, path in [('hyper-abi', sdk / 'share/hyper/abi'),
                              *[(name, sdk / 'share/hyper/rust' / name) for name in
                                ('hyper-os', 'hyper-rt', 'hyper-sys', 'hyper-filesystem')]]:
            patches[package] = {'path': str(path)}
        configs[name] = toml_document(data)
    return configs
