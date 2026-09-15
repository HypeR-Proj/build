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
    overlay = newc({'etc/hyper-volumes.conf': board.bootstrap_volumes().encode(),
                    'etc/hyper-clients.conf': board.bootstrap_clients().encode()})
    return original + gzip.compress(overlay, mtime=0)


def services(board, root):
    manifest = json.loads((root / 'app/init/config/services-io.json').read_bytes())
    manifest['virtual-machines']['config'] = '/data/vms.json'
    for service in manifest['services']:
        if service['name'] == 'vm-manager':
            service['capabilities'].append({
                'source': 'bootstrap.io-broker-client', 'purpose': 'io.broker-client',
                'operation': 'move', 'rights': ['wait', 'write']})
        if service['name'] != 'io-runtime':
            continue
        for capability in service['capabilities']:
            if capability['purpose'] == 'process.root-directory':
                capability['rights'] = ['read', 'write', 'execute', 'inspect', 'duplicate']
        service['capabilities'].append({
            'source': 'bootstrap.io-ready-channel', 'purpose': 'io.ready',
            'operation': 'move', 'rights': ['wait', 'write']})
        service['capabilities'].append({
            'source': 'bootstrap.io-broker-server', 'purpose': 'io.broker-server',
            'operation': 'move', 'rights': ['wait', 'read']})
    return manifest


def stage(board, root, output):
    output.mkdir(parents=True, exist_ok=True)
    clients = output / 'io-clients.conf'
    contents = board.bootstrap_clients()
    if not clients.exists() or clients.read_text() != contents:
        clients.write_text(contents)
    for name, value in [('board.json', board.source), ('services.json', services(board, root))]:
        contents = json.dumps(value, indent=2) + '\n'
        target = output / name
        if not target.exists() or target.read_text() != contents:
            target.write_text(contents)
