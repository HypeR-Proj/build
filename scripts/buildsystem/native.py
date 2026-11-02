# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Compile independent Native workspaces against canonical shared providers."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tomllib

from .shared import identities, providers, verify
from .link_context import prepare as link_context, preview
from . import lock_scope
from .shared_plan import prepare as shared_plan
from .native_cache import Cache
from .toolchain import native_environment
from native_libraries import read_image
from source_archive import sources


class Builder:
    def __init__(self, root, modules, output, arch, sdk, caches=None):
        self.root, self.modules, self.output, self.arch, self.sdk = root, modules, output, arch, sdk
        self.caches = caches or {domain: output / 'build/native' / arch / domain for domain in ('boot', 'system')}
        self.registry = providers(modules)
        self.library_names = {data.get('lib', {}).get('name', name.replace('-', '_')): name
                              for name, (_, _, data) in self.registry.items()}
        self.expected, self.products, self.active, self.records = {}, {}, set(), []
        self.helpers, self.helper_libraries = set(), set()
        self.provider_graph, self.provider_lock = None, None
        self._cache = None
        self._environment = None

    @property
    def cache(self):
        if self._cache is None:
            self._cache = Cache(self.root, self.modules, self.output, self.arch, self.sdk,
                                self.environment('system'))
        return self._cache

    def environment(self, domain, runtime='std'):
        if self._environment is None:
            self._environment = native_environment(self.root)
        return {**self._environment, 'CARGO_TARGET_DIR': str(self.caches[domain]),
                'HYPER_SYSROOT': str(self.sdk), 'HYPER_ARCH': self.arch,
                'HYPER_LINK_MODE': 'static' if domain == 'boot' else 'dynamic',
                'HYPER_RUST_STD': '0' if runtime == 'core' else '1'}

    def command(self, manifest, selections=()):
        return [str(self.sdk / 'bin/hyper-cargo'), 'build', '--manifest-path', str(manifest),
                '--release', '--locked', '--offline', *selections]

    def graph(self, command, domain, runtime):
        environment = self.environment(domain, runtime)
        def create():
            result = subprocess.run([*command, '-Z', 'unstable-options', '--unit-graph'], cwd=self.root,
                                    env=environment, capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stderr.strip())
            return json.loads(result.stdout)
        return self.cache.graph(command, environment, create)

    def scoped(self, manifest, workspace, label, command=None, owner=None):
        environment = self.environment('system')
        def resolve(command):
            self.cache.fetch([command], environment,
                             lambda arguments: subprocess.run(arguments, cwd=self.root, env=environment, check=True),
                             label='fetch-source-' + label)
        context = preview(manifest, workspace, self.caches['system'] / 'sources' / label,
                          self.sdk / 'bin/hyper-cargo', environment, self.cache.controls, resolve)
        command = list(command or self.command(manifest))
        command[command.index('--manifest-path') + 1] = str(context)
        while '--package' in command:
            index = command.index('--package')
            del command[index:index + 2]
        graph = self.graph(command, 'system', 'std')
        original = tomllib.loads((workspace.parent / 'Cargo.lock').read_text())
        resolved = tomllib.loads((context.parent / 'Cargo.lock').read_text())
        selected = lock_scope.validate(original, resolved, lock_scope.owned(graph, self.registry, owner))
        return command, graph, selected

    def normalized(self, command, manifest, workspace, label, graph=None):
        graph = graph or self.graph(command, 'system', 'std')
        context = link_context(manifest, workspace, graph, self.caches['system'] / 'contexts' / label,
                               self.sdk / 'bin/hyper-cargo', self.environment('system'),
                               self.provider_graph, self.provider_lock)
        if context != workspace:
            command = list(command)
            command[command.index('--manifest-path') + 1] = str(context)
            while '--package' in command:
                index = command.index('--package')
                del command[index:index + 2]
            graph = self.graph(command, 'system', 'std')
            helper_source = str(context.parent / 'link-context/src/lib.rs')
            for unit in graph['units']:
                if unit['target']['src_path'] == helper_source:
                    self.helpers.add(unit['pkg_id'])
                    self.helper_libraries.add('lib' + unit['target']['name'] + '.so')
            lock_scope.validate(tomllib.loads((workspace.parent / 'Cargo.lock').read_text()),
                                tomllib.loads((context.parent / 'Cargo.lock').read_text()),
                                lock_scope.owned(graph, self.registry, ignored=self.helpers))
        return command, identities(graph, self.helpers)

    def run(self, command, domain, runtime, label, action):
        log = self.caches[domain] / 'records' / (label + '.jsonl')
        log.parent.mkdir(parents=True, exist_ok=True)
        build_command = command
        environment = self.environment(domain, runtime)
        graph = self.graph(build_command, domain, runtime)
        archives = self.source_inputs(graph, environment)
        command = [*command, '--message-format=json-render-diagnostics']
        if action == 'check':
            command[1] = 'clippy'
            command += ['--', '-D', 'warnings']
        elif self.cache.restore(label, command, environment, log):
            self.check_products()
            self.records.append((domain, runtime, log))
            return log
        # Preparation may fetch pinned inputs; the compiler/build scripts remain
        # offline. Only dependencies in this exact Cargo graph need archives.
        for manifest, cache in archives:
            sources(manifest, cache, fetch=True)
        before = self.cache.before(graph) if action != 'check' else None
        with log.open('w') as stream:
            result = subprocess.run(command, cwd=self.root, env=environment, stdout=stream)
        if result.returncode:
            diagnostics = [json.loads(line).get('message', {}).get('rendered', '')
                           for line in log.read_text().splitlines()]
            raise ValueError(''.join(diagnostics).strip() or f'{label}: compiler exited {result.returncode}')
        messages = []
        for line in log.read_text().splitlines():
            message = json.loads(line)
            if message.get('package_id') in self.helpers:
                continue
            if action != 'check' and message.get('reason') == 'compiler-artifact':
                target = message['target']
                images = ([message['executable']] if message.get('executable') and 'bin' in target['kind'] else
                          [path for path in message['filenames'] if Path(path).suffix == '.so'
                           and 'dylib' in target['crate_types']])
                for path in images:
                    if self.helper_libraries & set(read_image(Path(path)).needed):
                        raise ValueError(f'{path}: compile-only link context leaked into runtime dependencies')
            messages.append(line)
        log.write_text('\n'.join(messages) + '\n')
        self.check_products()
        if action != 'check':
            self.cache.publish(label, command, environment, log, before)
        self.records.append((domain, runtime, log))
        return log

    def source_inputs(self, graph, environment):
        paths = [Path(unit['target']['src_path']) for unit in graph['units']]
        result, overrides = [], {}
        for module in self.modules.values():
            declaration = module.build.get('source_archives')
            if not declaration or not any(path.is_relative_to(module.source)
                                          or path.resolve().is_relative_to(module.source.resolve())
                                          for path in paths):
                continue
            # Prepared execution views link source files back to their owning
            # checkout. Preserve the logical module path when selecting inputs.
            manifest = module.source / declaration['manifest']
            variable = declaration['cache_env']
            cache = Path(environment.get(variable) or self.output / 'cache' / declaration['cache'])
            cache = (self.root / cache).resolve()
            if variable in overrides and overrides[variable] != str(cache):
                raise ValueError(f'conflicting source archive cache variable: {variable}')
            overrides[variable] = str(cache)
            result.append((manifest, cache))
        environment.update(overrides)
        return result

    def check_products(self):
        for name, (path, digest) in self.products.items():
            if self.cache.file(path) != digest:
                raise ValueError(f'Cargo replaced canonical shared library {name}; refusing incompatible publication')

    def provider(self, library, action):
        if library in self.expected:
            return
        if library not in self.library_names:
            raise ValueError(f'unknown Native shared provider: {library}')
        locks, available = [], []
        for name, (module, entry, _) in sorted(self.registry.items()):
            probe = module.source / entry['link_probe']
            command, _, locked = self.scoped(probe, probe, 'provider-' + name, owner=name)
            locks.append(locked)
            context = Path(command[command.index('--manifest-path') + 1])
            available.append(tomllib.loads((context.parent / 'Cargo.lock').read_text()))
        manifest = shared_plan(self.registry, self.caches['system'] / 'providers',
                               self.sdk / 'bin/hyper-cargo', self.environment('system'), locks, available)
        self.provider_graph = self.graph(self.command(manifest), 'system', 'std')
        self.provider_lock = tomllib.loads((manifest.parent / 'Cargo.lock').read_text())
        for (name, _), locked in zip(sorted(self.registry.items()), locks):
            lock_scope.validate(locked, self.provider_lock,
                                lock_scope.owned(self.provider_graph, self.registry, owner=name))
        command, graph = self.normalized(self.command(manifest), manifest, manifest,
                                         'system-libraries', self.provider_graph)
        if set(graph) != set(self.library_names):
            raise ValueError('canonical build does not match the registered shared provider set')
        record = self.run(command, 'system', 'std', 'system-libraries', action)
        if action != 'check':
            artifacts = [json.loads(line) for line in record.read_text().splitlines()]
            for name in graph:
                matches = [Path(path) for message in artifacts
                           if message.get('reason') == 'compiler-artifact' and message['target']['name'] == name
                           for path in message['filenames'] if Path(path).name == f'lib{name}.so']
                if len(matches) != 1:
                    raise ValueError(f'{name}: provider did not emit one shared library')
                self.products[name] = (matches[0], self.cache.file(matches[0]))
        self.expected = graph

    def build(self, module, entries, action='build', binaries=(), features=()):
        if len(entries) > 1:
            for entry in entries:
                self.build(module, [entry], action, binaries, features)
            return
        groups = {}
        for entry in entries:
            if entry.get('shared'):
                name = tomllib.loads((module.source / entry['manifest']).read_text())['package']['name']
                self.provider(next(key for key, value in self.library_names.items() if value == name), action)
                continue
            runtime = next((item.get('rust_runtime', 'std') for item in module.metadata.get('artifacts', [])
                            if item.get('manifest') == entry['manifest']), 'std')
            groups.setdefault((entry['domain'], runtime), []).append(entry)
        for (domain, runtime), selected in groups.items():
            arguments = []
            names = []
            for entry in selected:
                data = tomllib.loads((module.source / entry['manifest']).read_text())
                name = data['package']['name']
                names.append(name)
                arguments += ['--package', name]
                if entry.get('features'):
                    arguments += ['--features', ','.join(name + '/' + feature for feature in entry['features'])]
                if entry.get('default_features') is False:
                    arguments.append('--no-default-features')
            arguments += [value for binary in binaries for value in ('--bin', binary)]
            requested = [feature for feature in features if feature.split('/', 1)[0] in names]
            if requested:
                arguments += ['--features', ','.join(requested)]
            command = self.command(module.source / 'Cargo.toml', arguments)
            if domain == 'system' and runtime == 'std':
                command, initial, _ = self.scoped(module.source / selected[0]['manifest'],
                                                  module.source / 'Cargo.toml', names[0], command)
                libraries = identities(initial)
                if libraries:
                    self.provider(next(iter(libraries)), action)
                    command, graph = self.normalized(command, module.source / selected[0]['manifest'],
                                                     module.source / 'Cargo.toml', names[0], initial)
                    verify(self.expected, graph, module.name)
            else:
                fetch = [str(self.sdk / 'bin/hyper-cargo'), 'fetch', '--locked', '--manifest-path',
                         str(module.source / 'Cargo.toml')]
                environment = self.environment(domain, runtime)
                self.cache.fetch([fetch], environment,
                                 lambda command: subprocess.run(command, cwd=self.root, env=environment, check=True),
                                 label='fetch-' + module.name + '-' + runtime)
            selection = hashlib.sha256(','.join(names).encode()).hexdigest()[:12]
            self.run(command, domain, runtime, module.name + '-' + runtime + '-' + selection, action)

    def publish_records(self):
        """Publish the completed product's exact artifact graph for image assembly."""
        for domain in ('boot', 'system'):
            mode = 'static' if domain == 'boot' else 'dynamic'
            for runtime in ('core', 'std'):
                destination = self.caches['system'] / f'artifacts-{mode}-{runtime}.jsonl'
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(''.join(path.read_text() for d, r, path in self.records
                                               if (d, r) == (domain, runtime)))
