#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Build private ext4 image tools without installing conflicting UUID libraries."""

import fcntl
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile


VERSION = '1.47.3'
# https://www.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v1.47.3/sha256sums.asc
SHA256 = '7d4612f4e4f7ca6c2f669679028bcb02763e3b6280c9c19b2cf168eaf65e88af'
URL = (f'https://www.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/'
       f'v{VERSION}/e2fsprogs-{VERSION}.tar.gz')
TOOLS = ('mke2fs', 'debugfs')
HOST_TOOLS = Path(__file__).resolve().parents[1] / 'target/host-tools'


def ensure_e2fsprogs():
    """Publish both tools together; concurrent packers share one locked build."""
    host = platform.system().lower()
    if host not in ('darwin', 'linux'):
        raise ValueError(f'private e2fsprogs build is unsupported on {host}; install e2fsprogs')
    directory = HOST_TOOLS / f'e2fsprogs-{VERSION}-{host}-{platform.machine().lower()}'
    # Changes to this build recipe invalidate the cached installation.
    recipe = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    marker = directory / '.recipe'

    def ready():
        return (marker.is_file() and marker.read_text() == recipe
                and all((directory / 'sbin' / tool).is_file()
                        and os.access(directory / 'sbin' / tool, os.X_OK) for tool in TOOLS)
                and (directory / 'etc/mke2fs.conf').is_file())

    HOST_TOOLS.mkdir(parents=True, exist_ok=True)
    with Path(str(directory) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready():
            return directory / 'sbin'
        for tool in ('curl', 'make'):
            if shutil.which(tool) is None:
                raise ValueError(f'{tool} is required to build private e2fsprogs')
        cache = HOST_TOOLS / 'distfiles'
        cache.mkdir(exist_ok=True)
        archive = cache / f'e2fsprogs-{VERSION}.tar.gz'
        if not archive.is_file():
            print(f'Downloading e2fsprogs {VERSION}', file=sys.stderr, flush=True)
            with tempfile.TemporaryDirectory(prefix='.download-', dir=cache) as temporary:
                download = Path(temporary) / archive.name
                subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                                '--retry', '3', '--connect-timeout', '30', '--max-time', '300',
                                '--output', str(download), URL], check=True)
                if hashlib.sha256(download.read_bytes()).hexdigest() != SHA256:
                    raise ValueError('e2fsprogs archive checksum mismatch')
                download.replace(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise ValueError(f'e2fsprogs archive checksum mismatch; remove {archive} and retry')

        log = Path(str(directory) + '.build.log')
        print(f'Building private e2fsprogs {VERSION} (log: {log})', file=sys.stderr, flush=True)
        with tempfile.TemporaryDirectory(prefix='.e2fsprogs-', dir=HOST_TOOLS) as temporary:
            staging = Path(temporary)
            with tarfile.open(archive) as source:
                source.extractall(staging, filter='data')
            source = staging / f'e2fsprogs-{VERSION}'
            # These are host executables, independent of the kernel/SDK cross compiler.
            environment = os.environ.copy()
            for name in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LIBS'):
                environment.pop(name, None)
            environment.update(CC=os.environ.get('HOST_CC', '/usr/bin/clang' if host == 'darwin' else 'cc'),
                               CFLAGS='-O2', PKG_CONFIG='false')
            commands = [
                ['./configure', f'--prefix={directory}', f'--sysconfdir={directory / "etc"}',
                 '--enable-libuuid', '--enable-libblkid', '--disable-nls',
                 '--disable-fuse2fs', '--disable-uuidd', '--without-libarchive',
                 '--without-crond-dir', '--without-systemd-unit-dir',
                 'ac_cv_lib_magic_magic_file=no'],
                ['make', f'-j{min(os.cpu_count() or 1, 8)}', 'libs'],
                ['make', '-C', 'misc', 'mke2fs'],
                ['make', '-C', 'debugfs', 'debugfs'],
            ]
            with log.open('w') as output:
                try:
                    for command in commands:
                        subprocess.run(command, cwd=source, env=environment, check=True,
                                       stdout=output, stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as error:
                    raise ValueError(f'private e2fsprogs build failed; see {log}') from error
            install = staging / 'install'
            (install / 'sbin').mkdir(parents=True)
            (install / 'etc').mkdir()
            for tool, path in (('mke2fs', 'misc/mke2fs'), ('debugfs', 'debugfs/debugfs')):
                shutil.copy2(source / path, install / 'sbin' / tool)
            shutil.copy2(source / 'misc/mke2fs.conf', install / 'etc/mke2fs.conf')
            (install / '.recipe').write_text(recipe)
            if directory.exists():
                shutil.rmtree(directory)
            install.replace(directory)
        return directory / 'sbin'


if __name__ == '__main__':
    print(ensure_e2fsprogs())
