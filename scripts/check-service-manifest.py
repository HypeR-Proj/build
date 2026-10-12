#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate services.json with init's parser, capability policy and supervisor."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: check-service-manifest.py MANIFEST [ARCHIVE_PATH ...]')
    root = Path(__file__).resolve().parent.parent
    version = subprocess.check_output(['rustc', '-vV'], text=True)
    host = next(line.removeprefix('host: ') for line in version.splitlines()
                if line.startswith('host: '))
    arguments = sys.argv[2:]
    if '--core' in arguments:
        index = arguments.index('--core') + 1
        if index >= len(arguments):
            raise SystemExit('--core requires a manifest path')
        arguments[index] = str(Path(arguments[index]).resolve())
    environment = dict(os.environ, CARGO_TARGET_DIR=str(root / 'target/app-host-tests'))
    # Each supervisor owns its parser. Validate core manifests with bootstrap
    # and the combined graph with the formal supervisor's parser.
    system = '--core' in arguments
    workspace = 'app' if system else 'bootstrap/stage1'
    package = 'hyper-service-manager' if system else 'bootstrap-service-manager'
    result = subprocess.run([
        'cargo', 'run', '--quiet', '--locked', '--target', host,
        '-p', package, '--example', 'check-services', '--',
        str(Path(sys.argv[1]).resolve()), *arguments,
    ], cwd=root / workspace, env=environment)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
