# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Expand explicit Cargo workspace member patterns without resolving dependencies."""

from pathlib import Path
import tomllib


def member_directories(manifest_path):
    """Read declared members for source checks and component registration.

    This does not replace Cargo's dependency resolver or discover implicit path
    dependencies. Delivery libraries are explicitly registered in the workspace.
    Like Cargo, member patterns match directories (including hidden directories),
    while ordinary files are ignored. Non-package directories must be excluded.
    """
    manifest_path = Path(manifest_path)
    workspace = tomllib.loads(manifest_path.read_text())['workspace']
    base = manifest_path.parent
    excluded = {path.resolve() for pattern in workspace.get('exclude', [])
                for path in base.glob(pattern)}
    members = set()
    for pattern in workspace['members']:
        directories = [path.resolve() for path in base.glob(pattern) if path.is_dir()]
        if not directories:
            raise ValueError(f'workspace member does not exist: {pattern}')
        members.update(directory for directory in directories if directory not in excluded)
    return members
