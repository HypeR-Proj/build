#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Stage Native payloads and remove only packaged ELF debug information."""

import argparse
import filecmp
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inputs(args):
    return {
        "script": digest(__file__),
        "packer": digest(shutil.which(args.packer) or args.packer),
        "strip": digest(shutil.which(args.strip) or args.strip),
        "entries": args.entries,
        "contents": [digest(path) for path in args.entries[2::3]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packer", required=True)
    parser.add_argument("--strip", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("entries", nargs="+")
    args = parser.parse_args()
    if len(args.entries) % 3:
        parser.error("entries must be MODE ARCHIVE_PATH SOURCE triples")

    requested = inputs(args)
    state_path = Path(str(args.output) + ".build-state.json")
    try:
        previous = json.loads(state_path.read_bytes())
        if previous["inputs"] == requested and previous["output"] == digest(args.output):
            print(f"Native initramfs is up to date: {args.output}")
            return
    except (OSError, ValueError, KeyError):
        pass

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
        if inputs(args) != requested:
            raise RuntimeError("Native initramfs inputs changed while packing; retry the build")
        state = staging / "state.json"
        state.write_text(json.dumps({"inputs": requested, "output": digest(first)}, sort_keys=True))
        if not args.output.is_file() or not filecmp.cmp(first, args.output, shallow=False):
            first.replace(args.output)
        state.replace(state_path)


if __name__ == "__main__":
    main()
