# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Test-only selection for trimmed-product compilation and boot acceptance."""


def trimmed_product(entries, *, reporter=False):
    # These are the scenario's providers and diagnostic commands, not a product
    # preset. Required child programs come from the real component declarations.
    selected = {
        'component-manager', 'system-startup', 'console-raw-system', 'fs-backend-system',
        'fs-erofs-system', 'io-runtime-system', 'session-manager', 'mount-system',
        'cat', 'componentctl', 'free', 'journalctl', 'ls', 'ps',
    }
    if reporter:
        selected.add('test-report')
    available = {entry['staged']: entry for entry in entries
                 if 'binary' in entry and entry['environment'] == 'system'}
    pending = list(selected)
    while pending:
        name = pending.pop()
        if name not in available:
            raise ValueError(f'trimmed-product test requires registered program {name}')
        for dependency in available[name].get('runtime_requires', []):
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return {
        'version': 1,
        'kernel': {'features': ['runtime-symbols']},
        'boot': {'exclude': ['fs-fat']},
        'system': {'base': 'none', 'include': sorted(selected)},
    }


def write_product(path, source):
    """Write a CONFIG_ fixture, with structured runtime policy in a separate file."""
    import json
    import system_config

    product = system_config.ProductConfig(source)
    policy = {}
    if 'boot-profiles' in product.value:
        policy['boot-profiles'] = product.value['boot-profiles']
    if 'storage-providers' in product.value['system']:
        policy['storage-providers'] = product.value['system']['storage-providers']
    if policy:
        policy_path = path.with_suffix('.policy.json')
        policy_path.write_text(json.dumps(policy))
        product.policy_path = str(policy_path.resolve())
    data = system_config.format_config(product, system_config.registry_entries(), minimal=True)
    if not path.exists() or path.read_text() != data:
        path.write_text(data)
