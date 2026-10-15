# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Product composition of component instances and finite bootstrap adoption.

Bootstrap grants remain a separate deployment input. They are consumed once by
system-startup; they are not a dependency-ordering language in the manager.
"""

import copy
import re
from pathlib import Path

HEADER = {'SPDX-FileCopyrightText': '2026 roolrz', 'SPDX-License-Identifier': 'Apache-2.0'}

DESCRIPTIONS = {
    'system-startup': 'Transfer bootstrap ownership',
    'console-raw': 'Physical console transport',
    'journal': 'Service log collection',
    'session-manager': 'Terminal and interactive shell',
    'io-runtime': 'Storage and network backend',
    'fs-backend': 'Filesystem mount service',
    'mount': 'Mount persistent filesystems',
    'vm-manager': 'Virtual machine fleet',
    'bootstrap-retire': 'Release bootstrap resources',
}


def compose(core, formal):
    declarations = {}
    children = []

    def add(name, image, **program):
        if re.fullmatch(r'[A-Za-z0-9_.-]{1,63}', name) is None or name in declarations:
            raise ValueError(f'invalid or duplicate component name: {name!r}')
        declaration = {**HEADER, 'name': name, 'program': {'image': image, **program}}
        if description := DESCRIPTIONS.get(Path(image).name):
            declaration['description'] = description
        declarations[name] = declaration
        children.append({'name': name, 'component': name})
        return '/' + name

    add('startup', '/svc/system-startup', arguments=['/etc/hyper/startup.json'],
        startup='eager', resident=True)
    policy = {'providers': [], 'journal': '/journal', 'retirement': '/bootstrap-retire',
              'applications': []}
    for entry in core['services']:
        name = Path(entry['image']).name
        role = {'console-raw': 'console', 'io-runtime': 'io', 'fs-backend': 'filesystem'}.get(name)
        if role is not None:
            path = add(name, '/svc/' + name, prepared=True, resident=True)
            policy['providers'].append({'role': role, 'component': path})
    add('journal', '/svc/journal', prepared=True)
    if any(p['role'] == 'filesystem' for p in policy['providers']):
        policy['mounts'] = add('mounts', '/bin/mount', arguments=['-a'], prepared=True)
    add('bootstrap-retire', '/svc/bootstrap-retire', prepared=True)
    for entry in formal['services']:
        path = add(entry['name'], entry['image'], prepared=True)
        grants = [copy.deepcopy(g) for g in entry.get('capabilities', [])
                  if g['purpose'] not in ('stdio.input', 'stdio.output', 'stdio.error')]
        app = {'component': path, 'critical': entry.get('critical', False), 'capabilities': grants}
        if Path(entry['image']).name == 'session-manager':
            app['interactive'] = True
            for purpose in ('component.observe', 'component.control'):
                grants.append({'source': purpose, 'purpose': purpose, 'operation': 'duplicate',
                               'rights': ['write', 'wait', 'duplicate', 'transfer']})
        if any(g['purpose'] == 'vm.provisioning' for g in grants) and 'virtual-machines' in formal:
            app['fleet'] = formal['virtual-machines']['config']
        policy['applications'].append(app)
    protocol = {'type': 'protocol', 'protocol': 'hyper.journal.Query'}
    declarations['journal']['capabilities'] = [{'name': 'query', 'kind': protocol}]
    declarations['journal']['expose'] = [{'name': 'query', 'from': 'self', 'source': 'query', 'kind': protocol}]
    offers = []
    for name, declaration in declarations.items():
        if declaration['program']['image'] == '/svc/session-manager':
            declaration['use'] = [{'name': 'journal', 'kind': protocol}]
            offers.append({'name': 'journal', 'to': name, 'from': '#journal', 'source': 'query', 'kind': protocol})
    declarations['root'] = {**HEADER, 'name': 'root', 'children': children, 'offer': offers}
    return declarations, policy
