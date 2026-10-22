# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Use the kernel's dependency-free Kconfig parser for product configuration."""

from functools import lru_cache
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def backend():
    version = subprocess.check_output(['rustc', '-vV'], cwd=ROOT, text=True)
    host = next(line.removeprefix('host: ') for line in version.splitlines() if line.startswith('host: '))
    target = ROOT / 'target/host-tools/kconfig'
    binary = target / host / 'debug/hyper-kconfig'
    sources = list((ROOT / 'kernel/tools/kconfig').rglob('*.rs')) + [
        ROOT / 'kernel/tools/kconfig/Cargo.toml', ROOT / 'kernel/tools/kconfig/Cargo.lock',
        ROOT / 'rust-toolchain.toml']
    if not binary.exists() or any(path.stat().st_mtime_ns > binary.stat().st_mtime_ns for path in sources):
        subprocess.run(['cargo', 'build', '--quiet', '--locked', '--offline', '--manifest-path',
                        str(ROOT / 'kernel/tools/kconfig/Cargo.toml'), '--target', host,
                        '--target-dir', str(target)], cwd=ROOT, check=True)
    return binary


def query(command, *arguments, schema=None):
    result = subprocess.run([str(backend()), '--schema', str(schema or ROOT / 'Kconfig'),
                             command, *map(str, arguments)], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


@lru_cache(maxsize=1)
def schema():
    return {entry['name']: entry for entry in query('describe')['entries']}
