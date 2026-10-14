#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate services.json with init's parser, capability policy and supervisor."""
import json
import os
from pathlib import Path
import subprocess
import sys


def includes_formal_services(path):
    """Choose the policy owner; Rust remains the authoritative syntax checker."""
    try:
        source = json.loads(path.read_text())
        return bool(source.get('virtual-machines')) or any(
            service.get('phase') == 'system'
            or service.get('loader', 'dynamic') != 'bootstrap'
            for service in source['services'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Let the production parser report malformed input with its location.
        return False


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: check-service-manifest.py MANIFEST [--bootstrap | --core MANIFEST] [ARCHIVE_PATH ...]')
    root = Path(__file__).resolve().parent.parent
    version = subprocess.check_output(['rustc', '-vV'], text=True)
    host = next(line.removeprefix('host: ') for line in version.splitlines()
                if line.startswith('host: '))
    arguments = sys.argv[2:]
    bootstrap = '--bootstrap' in arguments
    if bootstrap:
        arguments.remove('--bootstrap')
        if '--core' in arguments:
            raise SystemExit('--bootstrap and --core are mutually exclusive')
    if '--core' in arguments:
        index = arguments.index('--core') + 1
        if index >= len(arguments):
            raise SystemExit('--core requires a manifest path')
        arguments[index] = str(Path(arguments[index]).resolve())
    environment = dict(os.environ)
    environment.setdefault('CARGO_TARGET_DIR', str(root / 'target/app-host-tests'))
    # Source deployments may mix boot and formal services. Their composition
    # belongs to formal init; the published core graph uses bootstrap policy.
    system = not bootstrap and ('--core' in arguments or includes_formal_services(Path(sys.argv[1])))
    workspace = 'app' if system else 'bootstrap/stage1'
    package = 'hyper-init' if system else 'hyper-bootstrap-stage1-init'
    result = subprocess.run([
        'cargo', 'run', '--quiet', '--locked', '--target', host,
        '-p', package, '--example', 'check-services', '--',
        str(Path(sys.argv[1]).resolve()), *arguments,
    ], cwd=root / workspace, env=environment)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
