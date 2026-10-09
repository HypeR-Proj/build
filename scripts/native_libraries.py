# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Verify the packaged Native ELF dependency and symbol closure before boot."""

from dataclasses import dataclass
from pathlib import Path
import struct


@dataclass
class Image:
    machine: int
    needed: list[str]
    interpreter: str | None
    soname: str | None
    exports: set[str]
    imports: set[str]


def read_image(path):
    with Path(path).open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            return None
        stream.seek(0)
        data = stream.read()
    if len(data) < 64 or data[4:6] != b'\x02\x01':
        raise ValueError(f'{path}: expected a little-endian ELF64 image')

    def unpack(format_, offset):
        if offset < 0 or offset + struct.calcsize(format_) > len(data):
            raise ValueError(f'{path}: truncated ELF metadata')
        return struct.unpack_from(format_, data, offset)

    phoff, = unpack('<Q', 32)
    phsize, phcount = unpack('<HH', 54)
    if phsize != 56:
        raise ValueError(f'{path}: unsupported program header size')
    segments = [unpack('<IIQQQQQQ', phoff + index * phsize) for index in range(phcount)]

    def region(offset, length):
        if offset < 0 or length < 0 or offset + length > len(data):
            raise ValueError(f'{path}: ELF range exceeds file')
        return data[offset:offset + length]

    def address(value, length):
        for kind, _, offset, start, _, size, _, _ in segments:
            if kind == 1 and start <= value and value - start + length <= size:
                return offset + value - start
        raise ValueError(f'{path}: dynamic metadata is outside file-backed LOAD segments')

    def string(table, offset):
        end = table.find(b'\0', offset)
        if offset < 0 or offset >= len(table) or end < 0:
            raise ValueError(f'{path}: unterminated ELF string')
        return table[offset:end].decode('utf-8')

    tags, needed = {}, []
    interpreter = None
    for kind, _, offset, _, _, size, _, _ in segments:
        if kind == 3:
            interpreter = string(region(offset, size), 0)
        if kind != 2:
            continue
        for position in range(offset, offset + size, 16):
            tag, value = unpack('<qQ', position)
            if tag == 0:
                break
            if tag == 1:
                needed.append(value)
            else:
                tags[tag] = value
    machine, = unpack('<H', 18)
    if not needed and 14 not in tags:
        return Image(machine, [], interpreter, None, set(), set())
    # Native dynamic images use SysV hash; its nchain is the dynsym count.
    if not {4, 5, 6, 10, 11} <= tags.keys() or tags[11] != 24:
        raise ValueError(f'{path}: missing Native dynamic symbol metadata')
    strings = region(address(tags[5], tags[10]), tags[10])
    _, count = unpack('<II', address(tags[4], 8))
    symbols = address(tags[6], count * 24)
    exports, imports = set(), set()
    for index in range(count):
        name, info, other, section, _, _ = unpack('<IBBHQQ', symbols + index * 24)
        binding, visibility = info >> 4, other & 3
        if not name or binding not in (1, 2):
            continue
        symbol = string(strings, name)
        if section and visibility in (0, 3):
            exports.add(symbol)
        elif not section and binding == 1:
            imports.add(symbol)
    return Image(machine, [string(strings, name) for name in needed], interpreter,
                 string(strings, tags[14]) if 14 in tags else None, exports, imports)


def validate(entries):
    links = {entries[index + 1]: entries[index + 2] for index in range(0, len(entries), 3)
             if entries[index] == 'symlink'}

    def resolve(name):
        # Resolve in the archive namespace, never against the build host.
        # Targets have already been restricted to canonical relative paths.
        for _ in range(41):
            parts = name.split('/')
            for index in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:index])
                if prefix in links:
                    name = '/'.join(parts[:index - 1] + [links[prefix]] + parts[index:])
                    break
            else:
                return name
        raise ValueError(f'archive symlink loop while resolving {name}')

    images = {entries[index + 1]: image for index in range(0, len(entries), 3)
              if entries[index] != 'symlink' and
              (image := read_image(entries[index + 2])) is not None}
    for root, image in images.items():
        arch = {183: 'aarch64', 243: 'riscv64', 62: 'x86_64'}.get(image.machine)
        if arch is None:
            raise ValueError(f'{root}: unsupported Native ELF machine {image.machine}')
        library_directory = f'lib/{arch}-hyper-hyper/'
        visited, pending = set(), [root]
        if image.interpreter:
            pending.append(image.interpreter.lstrip('/'))
        while pending:
            name = resolve(pending.pop())
            if name in visited:
                continue
            dependency = images.get(name)
            if dependency is None:
                raise ValueError(f'{root}: missing packaged ELF dependency {name}')
            if dependency.machine != image.machine:
                raise ValueError(f'{root}: dependency architecture mismatch: {name}')
            visited.add(name)
            for needed in dependency.needed:
                if not needed or '/' in needed or needed in ('.', '..'):
                    raise ValueError(f'{name}: invalid Native library name {needed!r}')
                destination = resolve(library_directory + needed)
                if destination in images and images[destination].soname != needed:
                    raise ValueError(f'{name}: library SONAME mismatch: {destination}')
                pending.append(destination)
        exports = set().union(*(images[name].exports for name in visited))
        missing = set().union(*(images[name].imports for name in visited)) - exports
        if missing:
            symbol = sorted(missing)[0]
            raise ValueError(f'{root}: unresolved dynamic symbol {symbol}; '
                             'rebuild and package applications and libraries together')
