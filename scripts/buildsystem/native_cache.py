# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Content-checked reuse of Native plans and completed compiler invocations."""

import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

from . import files


def digest(value):
    return hashlib.sha256(value).hexdigest()


class Cache:
    def __init__(self, root, modules, output, arch, sdk, environment):
        self.root, self.modules = root, modules
        self.directory = output / 'state' / arch / 'native'
        self.memo, self.owner_inputs = {}, {}
        prepared = json.loads((output / 'state' / arch / 'prepare.json').read_text())
        prefixes = {str(module.path): name for name, module in modules.items()}
        self.inventory = {}
        for name in prepared['generated']:
            parent = Path(name).parent
            while str(parent) not in prefixes and parent != parent.parent:
                parent = parent.parent
            owner = prefixes.get(str(parent))
            if owner:
                self.inventory.setdefault(owner, []).append(root / name)
        identity = {'controls': prepared['controls'], 'inventory': sorted(prepared['generated']),
                    'compiler': subprocess.check_output(['rustc', '-vV'], env=environment, text=True),
                    'llvm': {name: subprocess.check_output([environment[name], '--version'],
                                                           env=environment, text=True)
                             for name in ('HYPER_CLANG', 'HYPER_LD', 'LLVM_AR')},
                    'sdk': {name: self.file(sdk / 'share/hyper' / name)
                            for name in ('manifest', 'link-fingerprint', 'std-fingerprint')},
                    'driver': self.file(sdk / 'bin/hyper-cargo')}
        self.controls = digest(json.dumps(identity, sort_keys=True).encode())

    def file(self, path):
        path = Path(path)
        info = path.stat()
        stamp = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode
        old = self.memo.get(str(path))
        if old and old[0] == stamp:
            return old[1]
        value = digest(path.read_bytes())
        self.memo[str(path)] = stamp, value
        return value

    def owner(self, path):
        path = Path(path)
        candidates = [module for module in self.modules.values() if path.is_relative_to(module.source)]
        return max(candidates, key=lambda module: len(module.source.parts)).name if candidates else None

    def source(self, owner):
        inputs = {str(path): self.file(path) for path in self.inventory.get(owner, []) if path.is_file()}
        return digest(json.dumps(inputs, sort_keys=True).encode())

    def key(self, command, environment):
        manifest = Path(command[command.index('--manifest-path') + 1])
        controls = [manifest, manifest.parent / 'Cargo.lock', manifest.parent / 'link-context/Cargo.toml']
        value = [self.controls, command, environment,
                 {str(path): self.file(path) for path in controls if path.is_file()}]
        # Only the digest is persisted; environment values may contain credentials.
        return digest(json.dumps(value, sort_keys=True).encode())

    def graph(self, command, environment, create):
        key = self.key(command, environment)
        path = self.directory / 'graphs' / (key + '.json')
        if path.is_file():
            try:
                saved = json.loads(path.read_text())
                body = json.dumps(saved['graph'], sort_keys=True).encode()
                if digest(body) == saved['sha256']:
                    return saved['graph']
            except (ValueError, KeyError):
                pass
        graph = create()
        body = json.dumps(graph, sort_keys=True).encode()
        files.write(path, json.dumps({'graph': graph, 'sha256': digest(body)}).encode())
        return graph

    def fetch(self, commands, environment, run):
        """Reuse dependency downloads only while Cargo's registry cache exists.

        Source changes do not require another fetch. Lock/configuration changes
        or removal of registry/index data do. Git cache contents participate in
        the same inventory without assuming a particular checkout layout.
        """
        import os
        cargo = Path(environment.get('CARGO_HOME', Path.home() / '.cargo'))
        path = self.directory / 'fetch.json'
        def state():
            registry = []
            for directory in (cargo / 'registry/cache', cargo / 'registry/index', cargo / 'git'):
                for base, directories, filenames in os.walk(directory):
                    for name in sorted([*directories, *filenames]):
                        entry = Path(base) / name
                        try:
                            info = entry.stat()
                        except FileNotFoundError:
                            return None  # Another Cargo process is updating it.
                        registry.append((str(entry), info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            return {'keys': [self.key(command, environment) for command in commands], 'registry': digest(json.dumps(sorted(registry)).encode())}
        before = state()
        if before is not None and path.is_file():
            try:
                if json.loads(path.read_text()) == before:
                    return
            except ValueError:
                pass
        for command in commands:
            run(command)
        after = state()
        if after is not None:
            files.write(path, json.dumps(after).encode())

    def before(self, graph):
        owners = {self.owner(unit['target']['src_path']) for unit in graph['units']}
        owners.discard(None)
        # A local build script can read any file in its owning component, not
        # only the Rust files recorded in rustc's dep-info.
        return {name: self.source(name) for name in owners}

    def restore(self, label, command, environment, log):
        path = self.directory / 'results' / (label + '.json')
        if not path.is_file() or not log.is_file():
            return False
        try:
            saved = json.loads(path.read_text())
            return (saved['key'] == self.key(command, environment)
                    and saved['record'] == self.file(log)
                    and all(self.source(owner) == value for owner, value in saved['owners'].items())
                    and all(self.file(Path(name)) == value for name, value in saved['inputs'].items())
                    and all(self.file(Path(name)) == value for name, value in saved['outputs'].items()))
        except (OSError, ValueError, KeyError):
            return False

    def publish(self, label, command, environment, log, before):
        if any(self.source(owner) != value for owner, value in before.items()):
            raise ValueError('Native source inputs changed during compilation; retry the build')
        dependencies, outputs = set(), set()
        directory = Path(command[command.index('--manifest-path') + 1]).parent
        for line in log.read_text().splitlines():
            message = json.loads(line)
            if message.get('reason') != 'compiler-artifact':
                continue
            for filename in message['filenames']:
                path = Path(filename)
                if path.is_file():
                    outputs.add(path)
                candidates = {path.with_suffix('.d'), path.parent / (message['target']['name'] + '.d')}
                if path.name.startswith('lib'):
                    candidates.add(path.with_name(path.stem[3:] + '.d'))
                for depfile in candidates:
                    if not depfile.is_file():
                        continue
                    for rule in depfile.read_text().replace('\\\n', ' ').splitlines():
                        if rule.startswith('#'):
                            continue
                        parts = re.split(r'(?<!\\): ', rule, maxsplit=1)
                        if len(parts) == 2:
                            dependencies.update(directory / value.replace('$$', '$')
                                                for value in shlex.split(parts[1]))
        value = {'key': self.key(command, environment), 'owners': before,
                 'record': self.file(log),
                 'inputs': {str(path): self.file(path) for path in dependencies},
                 'outputs': {str(path): self.file(path) for path in outputs}}
        files.write(self.directory / 'results' / (label + '.json'), json.dumps(value, sort_keys=True).encode())
