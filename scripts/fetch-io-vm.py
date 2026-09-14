#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Import a digest-pinned I/O VM OCI boot package; never build or modify Linux."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import zlib
from pathlib import Path


ARTIFACT_TYPE = "application/vnd.hyper.io-vm.v1"
MIB = 1024 * 1024
LIMITS = {"Image": 32 * MIB, "initramfs.cpio.gz": 8 * MIB,
          "kernel.config": MIB, "sources.tar.xz": 2 * 1024 * MIB}
REFERENCE = re.compile(r"(ghcr\.io/[a-z0-9][a-z0-9._/-]*)@(sha256:[0-9a-f]{64})")
DEFAULT_LOCK = Path(__file__).with_name("io-vm.lock.json")


def pinned_reference(platform, lock=DEFAULT_LOCK):
    metadata = json.loads(lock.read_text())
    if metadata.get("version") != 1:
        raise ValueError("unsupported I/O VM lock format")
    entry = metadata["platforms"].get(platform)
    if entry is None:
        raise ValueError(f"no qualified I/O VM package pinned for {platform}")
    reference = entry["reference"]
    if not isinstance(reference, str) or REFERENCE.fullmatch(reference) is None:
        raise ValueError("I/O VM lock requires an immutable GHCR reference")
    return reference


def checked_file(path, descriptor):
    size = descriptor["size"]
    if path.is_symlink() or path.stat().st_size != size:
        raise ValueError(f"invalid size or symlink: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            digest.update(chunk)
    if "sha256:" + digest.hexdigest() != descriptor["digest"]:
        raise ValueError(f"checksum mismatch: {path.name}")


def validate_manifest(path, digest, platform):
    if path.stat().st_size > MIB:
        raise ValueError("oversized OCI manifest")
    data = path.read_bytes()
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("OCI manifest digest mismatch")
    manifest = json.loads(data)
    if (manifest.get("schemaVersion") != 2
            or manifest.get("artifactType") != ARTIFACT_TYPE):
        raise ValueError("unsupported I/O VM package format")
    annotations = manifest.get("annotations", {})
    if (annotations.get("org.hyper.architecture") != "aarch64"
            or annotations.get("org.hyper.platform") != platform):
        raise ValueError("I/O VM architecture/platform mismatch")
    layers = {}
    for entry in manifest["layers"]:
        name = entry.get("annotations", {}).get("org.opencontainers.image.title")
        if name not in LIMITS or name in layers:
            raise ValueError("unexpected or duplicate package payload")
        if (type(entry.get("size")) is not int or not 0 < entry["size"] <= LIMITS[name]
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry.get("digest", ""))):
            raise ValueError(f"invalid payload descriptor: {name}")
        layers[name] = entry
    if layers.keys() != LIMITS.keys():
        raise ValueError("incomplete package: runtime and corresponding sources required")
    return layers


def validate_runtime(directory, layers):
    for name in ("Image", "initramfs.cpio.gz"):
        checked_file(directory / name, layers[name])
    with (directory / "Image").open("rb") as stream:
        if stream.read(64)[56:60] != b"ARM\x64":
            raise ValueError("not an AArch64 Linux Image")
    compressed = (directory / "initramfs.cpio.gz").read_bytes()
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    expanded = decoder.decompress(compressed, 32 * MIB + 1)
    if (len(expanded) > 32 * MIB or not decoder.eof or decoder.unused_data
            or not expanded.startswith(b"070701")):
        raise ValueError("invalid or oversized initramfs")


def fetch(reference, platform, output, oras="oras"):
    match = REFERENCE.fullmatch(reference)
    if match is None:
        raise ValueError("use ghcr.io/OWNER/PACKAGE@sha256:DIGEST; tags are not accepted")
    repository, digest = match.groups()
    output.mkdir(parents=True, exist_ok=True)
    generation = output / digest.removeprefix("sha256:")
    if not generation.exists():
        with tempfile.TemporaryDirectory(prefix=".download-", dir=output) as temporary:
            staging = Path(temporary) / "package"
            staging.mkdir()
            subprocess.run([oras, "manifest", "fetch", "--output",
                            str(staging / "oci-manifest.json"), reference],
                           check=True, stdout=subprocess.DEVNULL)
            layers = validate_manifest(staging / "oci-manifest.json", digest, platform)
            # Source materials stay available in the same immutable OCI manifest,
            # but are neither downloaded here nor included in the Hyper ramdisk.
            for name in ("Image", "initramfs.cpio.gz"):
                subprocess.run([oras, "blob", "fetch", "--output", str(staging / name),
                                repository + "@" + layers[name]["digest"]],
                               check=True, stdout=subprocess.DEVNULL)
            validate_runtime(staging, layers)
            try:
                staging.rename(generation)
            except OSError:
                if not generation.is_dir():
                    raise
                # A concurrent importer may have committed this same digest.
                # Verify that generation below before accepting it.
    layers = validate_manifest(generation / "oci-manifest.json", digest, platform)
    validate_runtime(generation, layers)
    return generation.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", help="override the qualified package in io-vm.lock.json")
    parser.add_argument("--platform", required=True, choices=("qemu", "rpi5"))
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "target/io-vm/packages")
    parser.add_argument("--oras", default="oras")
    args = parser.parse_args()
    try:
        print(fetch(args.reference or pinned_reference(args.platform),
                    args.platform, args.output, args.oras))
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.CalledProcessError, zlib.error) as error:
        parser.exit(1, f"fetch-io-vm: {error}\n")


if __name__ == "__main__":
    main()
