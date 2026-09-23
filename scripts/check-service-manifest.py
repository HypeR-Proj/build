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
    environment = dict(os.environ, CARGO_TARGET_DIR=str(root / 'target/app-host-tests'))
    # Running in app/ uses its source patches, not an installed target SDK.
    result = subprocess.run([
        'cargo', 'run', '--quiet', '--locked', '--target', host,
        '-p', 'hyper-init', '--example', 'check-services', '--',
        str(Path(sys.argv[1]).resolve()), *sys.argv[2:],
    ], cwd=root / 'app', env=environment)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
