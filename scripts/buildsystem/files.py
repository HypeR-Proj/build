# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Atomic publication with stable timestamps for unchanged generated files."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile


def write(path, content, mode=0o644):
    path = Path(path)
    content = content.encode() if isinstance(content, str) else content
    if path.is_file() and not path.is_symlink() and path.read_bytes() == content:
        if path.stat().st_mode & 0o777 != mode:
            path.chmod(mode)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def link(path, source):
    path, source = Path(path), Path(source)
    target = os.path.relpath(source, path.parent)
    if path.is_symlink() and os.readlink(path) == target:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ('.' + path.name + f'-link-{os.getpid()}')
    try:
        temporary.symlink_to(target)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


@contextmanager
def lock(output, *, shared=False):
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.prepare.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
