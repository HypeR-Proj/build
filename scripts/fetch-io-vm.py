#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Import a digest-pinned I/O VM OCI boot package; never build or modify Linux."""

import argparse
import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib
from pathlib import Path


ARTIFACT_TYPE = "application/vnd.hyper.io-vm.v1"
MIB = 1024 * 1024
LIMITS = {"Image": 32 * MIB, "initramfs.cpio.gz": 8 * MIB,
          "kernel.config": MIB, "sources.tar.xz": 2 * 1024 * MIB}
REFERENCE = re.compile(r"(ghcr\.io/[a-z0-9][a-z0-9._/-]*)@(sha256:[0-9a-f]{64})")
DEFAULT_LOCK = Path(__file__).with_name("io-vm.lock.json")
ORAS_VERSION = "1.3.0"
# Published checksums from oras-project/oras v1.3.0.
ORAS_SHA256 = {
    "darwin_amd64": "82c33f7da8430ea7fa7e7bdf7721be0a0d0481e5ccb2472ea438490d5e8641a9",
    "darwin_arm64": "e10c6552c02d5a7c7eaf7170d3b6f7f094b675a98a1e0edf4d4478a909447245",
    "linux_amd64": "6cdc692f929100feb08aa8de584d02f7bcc30ec7d88bc2adc2054d782db57c64",
    "linux_arm64": "7649738b48fde10542bcc8b0e9b460ba83936c75fb5be01ee6d4443764a14352",
}


def ensure_oras():
    """Prefer PATH; otherwise atomically install a verified host tool locally."""
    installed = shutil.which("oras")
    if installed:
        return installed
    machine = host_platform.machine().lower()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    host = host_platform.system().lower() + "_" + architecture
    checksum = ORAS_SHA256.get(host)
    if checksum is None:
        raise ValueError(f"automatic ORAS installation is unsupported on {host}; set --oras")
    directory = (Path(__file__).resolve().parents[1] / "target/tools"
                 / f"oras-{ORAS_VERSION}-{host}")
    executable = directory / "oras"
    if executable.is_file() and os.access(executable, os.X_OK):
        return str(executable)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"oras_{ORAS_VERSION}_{host}.tar.gz"
    url = f"https://github.com/oras-project/oras/releases/download/v{ORAS_VERSION}/{filename}"
    print(f"Installing ORAS {ORAS_VERSION} for {host}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=directory) as temporary:
        staging = Path(temporary)
        archive = staging / filename
        subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                        "--retry", "3", "--connect-timeout", "30", "--max-time", "300",
                        "--output", str(archive), url], check=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
            raise ValueError("ORAS archive checksum mismatch")
        # Copy just the regular executable; never extract archive paths or links.
        with tarfile.open(archive, "r:gz") as package:
            member = package.getmember("oras")
            if not member.isfile() or not 0 < member.size <= 64 * MIB:
                raise ValueError("invalid ORAS executable in archive")
            with package.extractfile(member) as source, (staging / "oras").open("wb") as target:
                shutil.copyfileobj(source, target)
        (staging / "oras").chmod(0o755)
        (staging / "oras").replace(executable)
    return str(executable)


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
    legacy = annotations.get("org.hyper.platform")
    supported = [legacy]
    if "org.hyper.supported-platforms" in annotations:
        supported = json.loads(annotations["org.hyper.supported-platforms"])
        if (not isinstance(supported, list) or not supported
                or any(value not in ("qemu", "rpi5") for value in supported)
                or len(set(supported)) != len(supported) or legacy not in supported):
            raise ValueError("invalid I/O VM supported-platforms metadata")
    if (annotations.get("org.hyper.architecture") != "aarch64"
            or platform not in supported):
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


def fetch(reference, platform, output, oras=""):
    match = REFERENCE.fullmatch(reference)
    if match is None:
        raise ValueError("use ghcr.io/OWNER/PACKAGE@sha256:DIGEST; tags are not accepted")
    repository, digest = match.groups()
    output.mkdir(parents=True, exist_ok=True)
    generation = output / digest.removeprefix("sha256:")
    if not generation.exists():
        if not oras:
            oras = ensure_oras()
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
    parser.add_argument("--oras", default="", help="ORAS executable; default: PATH or verified local download")
    args = parser.parse_args()
    try:
        print(fetch(args.reference or pinned_reference(args.platform),
                    args.platform, args.output, args.oras))
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.CalledProcessError, tarfile.TarError, zlib.error) as error:
        parser.exit(1, f"fetch-io-vm: {error}\n")


if __name__ == "__main__":
    main()
