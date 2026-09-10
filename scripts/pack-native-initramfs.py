#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Stage Native payloads and remove only packaged ELF debug information."""

import argparse
import filecmp
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packer", required=True)
    parser.add_argument("--strip", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("entries", nargs="+")
    args = parser.parse_args()
    if len(args.entries) % 3:
        parser.error("entries must be MODE ARCHIVE_PATH SOURCE triples")

    # Keep staging beside the destination so publication is an atomic rename.
    # Never strip the original application or SDK build products.
    with tempfile.TemporaryDirectory(
        prefix=".native-initramfs-", dir=args.output.parent
    ) as temporary:
        staging = Path(temporary)
        entries = []
        for index in range(0, len(args.entries), 3):
            mode, name, source = args.entries[index : index + 3]
            destination = staging / str(index)
            shutil.copyfile(source, destination)
            with destination.open("rb") as payload:
                is_elf = payload.read(4) == b"\x7fELF"
            if is_elf:
                subprocess.run([args.strip, "--strip-debug", str(destination)], check=True)
            entries.extend([mode, name, str(destination)])

        first = staging / "first.cpio"
        second = staging / "second.cpio"
        for archive in (first, second):
            with archive.open("wb") as output:
                subprocess.run([args.packer, *entries], stdout=output, check=True)
        if not filecmp.cmp(first, second, shallow=False):
            raise RuntimeError("Native initramfs packing is not deterministic")
        first.replace(args.output)


if __name__ == "__main__":
    main()
