#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate an I/O VM appliance and compose HypeR-owned guest FIT images."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import zlib

MIB = 1024 * 1024

def bounded_read(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError(f'empty or oversized artifact: {path}')
    return data


def package_payloads(package):
    """Accept a complete external boot generation or a verified OCI import."""
    boot = package / 'boot-artifacts.json'
    if boot.exists():
        metadata = json.loads(bounded_read(boot, MIB))
        if metadata.get('format') != 1 or metadata.get('architecture') != 'aarch64':
            raise ValueError('unsupported boot artifact format or architecture')
        paths = []
        for field, pattern, limit in (
                ('kernel', r'Image-([0-9a-f]{64})', 32 * MIB),
                ('initramfs', r'initramfs-([0-9a-f]{64})\.cpio\.gz', 8 * MIB)):
            match = re.fullmatch(pattern, metadata[field])
            if match is None:
                raise ValueError('invalid content-addressed boot filename')
            path = package / metadata[field]
            data = bounded_read(path, limit)
            if hashlib.sha256(data).hexdigest() != match[1]:
                raise ValueError(f'boot artifact checksum mismatch: {field}')
            paths.append(path)
        image, initramfs = paths
    else:
        # Reuse the importer contract rather than independently interpreting
        # its layer names and source-material requirements.
        source = Path(__file__).with_name('fetch-io-vm.py')
        spec = importlib.util.spec_from_file_location('hyper_io_package', source)
        importer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(importer)
        layers = importer.validate_manifest(package / 'oci-manifest.json',
                                            'sha256:' + package.name, 'qemu')
        importer.validate_runtime(package, layers)
        image, initramfs = package / 'Image', package / 'initramfs.cpio.gz'
    if bounded_read(image, 32 * MIB)[56:60] != b'ARM\x64':
        raise ValueError('not an AArch64 Linux Image')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    expanded = decoder.decompress(bounded_read(initramfs, 8 * MIB), 32 * MIB + 1)
    if (len(expanded) > 32 * MIB or not decoder.eof or decoder.unused_data
            or not expanded.startswith(b'070701')):
        raise ValueError('invalid complete initramfs')
    return image.resolve(), initramfs.resolve()


def prepare(package, fit_pack, output, test="basic"):
    image, initramfs = package_payloads(package)
    output.mkdir(parents=True, exist_ok=True)
    for role in ('io', 'business'):
        bootargs = ('console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 '
                    f'rdinit=/init loglevel=7 hyper.role={role} hyper.test={test}')
        subprocess.run([str(fit_pack), str(output / f'{role}.itb'), 'arm64',
                        str(64 * MIB), '1', str(image), '0x40200000', '0x40200000',
                        str(initramfs), bootargs], check=True)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--fit-pack', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image, initramfs = package_payloads(args.package)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(args.fit_pack), str(args.output), 'arm64', str(64 * MIB), '1',
                    str(image), '0x40200000', '0x40200000', str(initramfs),
                    'console=ttyAMA0 earlycon=pl011,mmio32,0x09000000 rdinit=/init '
                    'loglevel=4 hyper.role=io hyper.mode=standby'], check=True)


if __name__ == '__main__':
    main()
