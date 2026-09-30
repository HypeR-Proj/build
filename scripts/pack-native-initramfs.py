#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Stage Native payloads and remove only packaged ELF debug information."""

import argparse
import filecmp
import hashlib
import json
import importlib.util
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_entries(path):
    with path.open('rb') as source:
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('archive entry manifest exceeds 1 MiB')
    records = json.loads(data)
    if not isinstance(records, list):
        raise ValueError('archive entry manifest must contain a list of triples')
    entries = []
    for record in records:
        if (not isinstance(record, list) or len(record) != 3
                or any(not isinstance(field, str) for field in record)):
            raise ValueError('archive entries must be MODE ARCHIVE_PATH SOURCE triples')
        if not Path(record[2]).is_absolute():
            raise ValueError('archive entry manifest sources must be absolute paths')
        entries.extend(record)
    return entries, hashlib.sha256(data).hexdigest()


def validate_entries(entries):
    destinations = set()
    for index in range(0, len(entries), 3):
        mode, name, source = entries[index:index + 3]
        if not re.fullmatch(r'0[0-7]{3}', mode):
            raise ValueError(f'invalid archive mode: {mode}')
        if (not name or name.startswith('/') or '\0' in name
                or any(part in ('', '.', '..') for part in name.split('/'))):
            raise ValueError(f'invalid archive path: {name}')
        if not source or '\0' in source:
            raise ValueError(f'invalid archive source: {source}')
        if name in destinations:
            raise ValueError(f'duplicate archive path: {name}')
        destinations.add(name)
    for name in destinations:
        if any(str(parent) in destinations for parent in PurePosixPath(name).parents):
            raise ValueError(f'archive file shadows a directory: {name}')


def inputs(args):
    return {
        "script": digest(__file__),
        "packer": digest(shutil.which(args.packer) or args.packer),
        "strip": digest(shutil.which(args.strip) or args.strip),
        "deployment": ([digest(args.deployment), digest(Path(__file__).with_name("app-deployment.py"))]
                       if args.deployment else None),
        "entry_manifests": [[str(path), digest(path)] for path in args.entries_from],
        "entries": args.entries,
        "contents": [digest(path) for path in args.entries[2::3]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packer", required=True)
    parser.add_argument("--strip", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--profile", choices=["system", "development"], default="development")
    for root in ("apps", "sdk", "std", "arch"):
        parser.add_argument("--" + root)
    parser.add_argument("--replace", action="append", default=[])
    parser.add_argument("--entries-from", type=Path, action="append", default=[],
                        help="JSON list of MODE ARCHIVE_PATH absolute-SOURCE triples")
    parser.add_argument("entries", nargs="*")
    args = parser.parse_args()
    if len(args.entries) % 3:
        parser.error("entries must be MODE ARCHIVE_PATH SOURCE triples")
    loaded_manifests = []
    for path in args.entries_from:
        entries, checksum = load_entries(path)
        args.entries.extend(entries)
        loaded_manifests.append([str(path), checksum])

    if args.deployment:
        roots = {key: getattr(args, key) for key in ("apps", "sdk", "std", "arch")}
        if not all(roots.values()):
            parser.error("deployment requires --apps, --sdk, --std and --arch")
        spec = importlib.util.spec_from_file_location(
            "app_deployment", Path(__file__).with_name("app-deployment.py"))
        deployment = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deployment)
        args.entries = deployment.compose(args.deployment, args.profile, roots, args.replace) + args.entries
    elif args.replace:
        parser.error("--replace requires --deployment")
    if not args.entries:
        parser.error("no initramfs entries selected")
    validate_entries(args.entries)
    # Validate the actual selected/generated manifest before cache hits or any
    # output mutation. The host tool shares init's parser and admission policy.
    manifests = [args.entries[index + 2] for index in range(0, len(args.entries), 3)
                 if args.entries[index + 1].lstrip('/') == 'etc/hyper/services.json']
    if len(manifests) > 1:
        parser.error("duplicate etc/hyper/services.json entries")
    if manifests:
        subprocess.run([sys.executable, str(Path(__file__).with_name('check-service-manifest.py')),
                        manifests[0], *args.entries[1::3]], check=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    requested = inputs(args)
    if requested["entry_manifests"] != loaded_manifests:
        raise RuntimeError("Native entry manifests changed while loading; retry the build")
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
