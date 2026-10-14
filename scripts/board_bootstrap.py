# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Generate bootstrap projections without reading the not-yet-mounted data volume."""

import gzip
import json
from pathlib import Path


def newc(files):
    """Small regular-file overlay; Linux supports concatenated newc archives."""
    output = bytearray()
    records = [('etc', 0o40755, b'')]
    records.extend((name, 0o100644, content) for name, content in sorted(files.items()))
    records.append(('TRAILER!!!', 0, b''))
    for inode, (name, mode, contents) in enumerate(records, 1):
        encoded = name.encode() + b'\0'
        output.extend(b'\0' * (-len(output) % 4))
        fields = (inode, mode, 0, 0, 1, 0, len(contents), 0, 0, 0, 0, len(encoded), 0)
        output.extend(b'070701' + ''.join(f'{field:08x}' for field in fields).encode())
        output.extend(encoded)
        output.extend(b'\0' * (-len(output) % 4))
        output.extend(contents)
    output.extend(b'\0' * (-len(output) % 4))
    return bytes(output)


def linux_overlay(board, original):
    # Keep the verified upstream member intact, including its hard-link inode
    # table. A separate compressed archive contributes only HypeR configuration.
    overlay = newc({'etc/hyper-clients.conf': board.backend_clients().encode(),
                    'etc/hyper-networks.conf': board.bootstrap_networks().encode()})
    return original + gzip.compress(overlay, mtime=0)


def services(board, root):
    manifest = json.loads((root / 'bootstrap/stage1/init/config/services-with-vms.json').read_bytes())
    manifest['virtual-machines']['config'] = '/data/vms.json'
    manifest['services'].append(filesystem_service())
    for service in manifest['services']:
        if service['name'] == 'session-manager':
            service['capabilities'].append({
                'source': 'bootstrap.filesystem-control-client',
                'purpose': 'filesystem.control-client', 'operation': 'duplicate',
                'rights': ['wait', 'write', 'duplicate', 'transfer']})
        if service['name'] == 'vm-manager':
            service['capabilities'].append({
                'source': 'bootstrap.io-broker-client', 'purpose': 'io.broker-client',
                'operation': 'move', 'rights': ['wait', 'write']})
        if service['name'] != 'io-runtime':
            continue
        service['after'].append('fs-backend')
        service['capabilities'].append({
            'source': 'bootstrap.filesystem-provider', 'purpose': 'filesystem.provider',
            'operation': 'move', 'rights': ['wait', 'write']})
        service['capabilities'].append({
            'source': 'bootstrap.io-ready-channel', 'purpose': 'io.ready',
            'operation': 'move', 'rights': ['wait', 'write']})
        service['capabilities'].append({
            'source': 'bootstrap.io-broker-server', 'purpose': 'io.broker-server',
            'operation': 'move', 'rights': ['wait', 'read', 'transfer']})
    return manifest


def filesystem_service():
    """A format-independent manager delegates each volume to an isolated worker."""
    def capability(source, purpose, rights, operation='duplicate'):
        return {'source': 'bootstrap.' + source, 'purpose': purpose,
                'operation': operation, 'rights': rights}

    return {
        'name': 'fs-backend', 'image': '/svc/fs-backend', 'loader': 'bootstrap',
        'critical': False, 'restart': 'never', 'after': ['console-raw'],
        'capabilities': [
            capability('filesystem-attach', 'filesystem.attach', ['wait', 'read'], 'move'),
            capability('filesystem-control-server', 'filesystem.control-server',
                       ['wait', 'read', 'transfer'], 'move'),
            capability('service-output-channel', 'stdio.output',
                       ['wait', 'write', 'duplicate', 'transfer']),
            capability('service-output-channel', 'stdio.error',
                       ['wait', 'write', 'duplicate', 'transfer']),
            capability('root-directory', 'process.root-directory',
                       ['read', 'write', 'execute', 'protect', 'inspect', 'duplicate', 'transfer']),
            capability('task-factory', 'process.task-factory',
                       ['create-process', 'create-task-group']),
            capability('resource-domain', 'process.resource-domain',
                       ['create-resource-domain']),
            capability('dynamic-library-directory', 'process.child-library-directory',
                       ['read', 'execute', 'protect', 'duplicate', 'transfer']),
            capability('environment-directory', 'process.environment-directory',
                       ['read', 'inspect', 'execute', 'protect', 'duplicate', 'transfer']),
            capability('child-loader', 'process.child-loader',
                       ['read', 'execute', 'duplicate', 'transfer']),
        ],
    }


def stage(board, root, output):
    output.mkdir(parents=True, exist_ok=True)
    clients = output / 'io-clients.conf'
    contents = board.bootstrap_clients()
    if not clients.exists() or clients.read_text() != contents:
        clients.write_text(contents)
    for name, value in [('services.json', services(board, root)),
                        ('vms.json', board.vms()),
                        ('storage.json', board.storage_policy()),
                        ('filesystem.json', {'worker_directories': [
                            {'environment': 'svc'}]})]:
        contents = json.dumps(value, indent=2) + '\n'
        target = output / name
        if not target.exists() or target.read_text() != contents:
            target.write_text(contents)
    for name in ('boot-fstab', 'fstab'):
        (output / name).write_text(board.source['native'][name])
