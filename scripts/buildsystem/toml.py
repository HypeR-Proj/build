# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Serialize the JSON-compatible subset used by generated Cargo manifests."""

import json
import re


def key(value):
    return value if re.fullmatch(r'[A-Za-z0-9_-]+', value) else json.dumps(value)


def scalar(value):
    if isinstance(value, dict):
        return '{' + ', '.join(key(k) + ' = ' + scalar(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(scalar(item) for item in value) + ']'
    if isinstance(value, (str, bool, int)):
        return json.dumps(value, ensure_ascii=False)
    raise ValueError(f'unsupported Cargo manifest value: {type(value).__name__}')


def dumps(value):
    lines = []

    def table(path, entries):
        if path:
            lines.append('[' + '.'.join(key(part) for part in path) + ']')
        for name, item in entries.items():
            if not isinstance(item, dict):
                lines.append(key(name) + ' = ' + scalar(item))
        lines.append('')
        for name, item in entries.items():
            if isinstance(item, dict):
                table([*path, name], item)

    table([], value)
    return '\n'.join(lines)
