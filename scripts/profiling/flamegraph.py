#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Convert HypeR PMU JSON-lines into folded stacks and a standalone SVG.

Symbolization uses the exact kernel ELF, independently of its runtime KASLR
address. Only kernel frame pointers are unwound; user and guest samples retain
explicit world labels. This tool never downloads a symbolizer or strips an ELF.
"""
import argparse
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys


class Symbols:
    def __init__(self, elf, tool, kernel):
        data = elf.read_bytes()
        # Both strings are embedded by the boot version report. A dirty build
        # additionally needs its timestamp, not merely the parent git revision.
        identity = kernel.split()
        if len(identity) != 2 or any(item.encode() not in data for item in identity):
            raise ValueError('kernel ELF does not match capture revision/build timestamp')
        output = subprocess.run([tool, '--numeric-sort', '--print-size', '--defined-only',
                                 '--demangle', str(elf)], check=True, capture_output=True,
                                text=True).stdout
        symbols = []
        base = None
        for line in output.splitlines():
            parts = line.split(None, 3)
            if len(parts) != 4:
                continue
            address, size, kind, name = parts
            address, size = int(address, 16), int(size, 16)
            if name == '__image_start':
                base = address
            if kind.lower() in ('t', 'w') and size:
                symbols.append((address, size, name))
        if base is None or not symbols:
            raise ValueError('ELF lacks __image_start or function symbols; use the unstripped kernel ELF')
        symbols.sort()
        self.base = base
        self.symbols = symbols
        self.addresses = [item[0] for item in symbols]

    def resolve(self, offset):
        address = self.base + offset
        index = bisect_right(self.addresses, address) - 1
        if index >= 0:
            start, size, name = self.symbols[index]
            if address < start + size:
                return name.replace(';', ':').replace('\n', ' ')
        return f'[kernel+0x{offset:x}]'


def unsigned(value, name, maximum=(1 << 64) - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'invalid {name}')
    return value


def captures(lines, resolve):
    """Validate one capture, preserve unknown frames, and expose loss totals."""
    counts = Counter()
    header = None
    summary = None
    previous = {}
    irq_drops = {}
    overwritten = 0
    samples = 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('expected an object')
            if header is None:
                if row.get('format') != 'hyper-pmu-v1' or row.get('address') != 'kernel-image-offset' or row.get('event') != 'cpu-cycles':
                    raise ValueError('unsupported capture format/event/address type')
                if (not 1 <= unsigned(row['cpus'], 'CPU count') <= 64
                        or not 1_000_000 <= unsigned(row['period'], 'period') <= 1_000_000_000):
                    raise ValueError('invalid capture configuration')
                header = row
                continue
            if summary is not None:
                raise ValueError('data after capture end')
            if row.get('end') is True:
                summary = row
                continue
            cpu, sequence, world = row['cpu'], row['sequence'], row['world']
            for key in ('cpu', 'sequence', 'dropped', 'world'):
                unsigned(row[key], key)
            if type(row['incomplete']) is not bool:
                raise ValueError('invalid callchain flag')
            frames = row['frames']
            if not 0 <= cpu < header['cpus'] or world not in (0, 1, 2) or not isinstance(frames, list) or len(frames) > 16:
                raise ValueError('invalid sample')
            if world != 0 and frames:
                raise ValueError('non-kernel frames are unsupported')
            expected = previous.get(cpu, -1) + 1
            if sequence < expected or row['dropped'] < irq_drops.get(cpu, 0):
                raise ValueError('non-monotonic sample sequence/counters')
            overwritten += sequence - expected
            previous[cpu] = sequence
            irq_drops[cpu] = row['dropped']
            stack = [f'cpu{cpu}', ('kernel', '[Native user: no unwind]', '[guest: no unwind]')[world]]
            if row['incomplete']:
                stack.append('[incomplete callchain]')
            offsets = [int(value, 16) for value in frames]
            if any(value < 0 or value >= 1 << 64 for value in offsets):
                raise ValueError('invalid image offset')
            stack.extend(resolve(value) for value in reversed(offsets))
            if world == 0 and not offsets:
                stack.append('[kernel PC unavailable]')
            counts[';'.join(stack)] += 1
            samples += 1
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f'line {number}: {error}') from error
    if header is None or summary is None:
        raise ValueError('incomplete capture: metadata or end record missing')
    if (unsigned(summary.get('samples'), 'final samples') != samples
            or unsigned(summary.get('overwritten'), 'final overwrites') != overwritten):
        raise ValueError('capture summary does not match sample records')
    if unsigned(summary.get('irq_dropped'), 'final IRQ drops') < sum(irq_drops.values()):
        raise ValueError('invalid final IRQ drop count')
    return counts, {'samples': samples, 'overwritten': overwritten,
                    'irq_dropped': summary['irq_dropped'], 'kernel': header['kernel']}


@dataclass
class Node:
    count: int = 0
    children: dict = field(default_factory=dict)


def svg(stacks, metadata):
    """Classic aggregated flame graph: x is sample share, not elapsed time."""
    root = Node()
    depth = 0
    for stack, count in stacks.items():
        names = stack.split(';')
        depth = max(depth, len(names))
        node = root
        node.count += count
        for name in names:
            node = node.children.setdefault(name, Node())
            node.count += count
    if not root.count:
        raise ValueError('no samples captured; check PMU support, workload and period')
    width, row = 1200, 22
    height = (depth + 1) * row + 85
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<style>text{font:12px monospace}g:hover rect{stroke:black;stroke-width:1.5}</style>',
           '<rect width="100%" height="100%" fill="#fafafa"/>',
           '<text x="10" y="22" font-weight="bold">HypeR PMU — CPU cycles (sample counts)</text>',
           f'<text x="10" y="42">{html.escape(metadata["kernel"])}</text>',
           f'<text x="10" y="62">Samples: {root.count}; overwritten: {metadata["overwritten"]}; IRQ contention drops: {metadata["irq_dropped"]}</text>']

    def draw(parent, x, span, level):
        for name, node in sorted(parent.children.items()):
            size = span * node.count / parent.count
            y = height - 15 - row * (level + 1)
            hue = sum(name.encode()) % 45
            title = f'{name}: {node.count} samples ({100 * node.count / root.count:.2f}%)'
            out.append(f'<g><title>{html.escape(title)}</title><rect x="{x:.3f}" y="{y}" width="{size:.3f}" height="{row-1}" fill="hsl({hue},85%,70%)"/>')
            if size > 12:
                label = name[:max(0, int(size / 7) - 2)]
                out.append(f'<text x="{x+3:.3f}" y="{y+15}">{html.escape(label)}</text>')
            out.append('</g>')
            draw(node, x, size, level + 1)
            x += size
    draw(root, 0, width, 0)
    out.append('</svg>')
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--kernel', type=Path, required=True, help='exact unstripped kernel ELF from this build')
    parser.add_argument('--nm', help='llvm-nm path (auto-detected by default)')
    parser.add_argument('--output', type=Path, required=True, help='SVG destination; folded stacks are written beside it')
    args = parser.parse_args()
    try:
        tool = args.nm or shutil.which('llvm-nm')
        if not tool and Path('/opt/homebrew/opt/llvm/bin/llvm-nm').is_file():
            tool = '/opt/homebrew/opt/llvm/bin/llvm-nm'
        if not tool:
            raise ValueError('llvm-nm not found; pass --nm PATH')
        with args.capture.open() as source:
            header = json.loads(next(line for line in source if line.strip()))
        symbols = Symbols(args.kernel, tool, header['kernel'])
        with args.capture.open() as source:
            stacks, metadata = captures(source, symbols.resolve)
        figure = svg(stacks, metadata)
        args.output.write_text(figure)
        args.output.with_suffix('.folded').write_text(''.join(f'{stack} {count}\n' for stack, count in sorted(stacks.items())))
        print(f'{args.output}: {metadata["samples"]} samples, {metadata["overwritten"]} overwritten, {metadata["irq_dropped"]} IRQ drops')
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        parser.exit(1, f'flamegraph: {error}\n')


if __name__ == '__main__':
    main()
