#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Package import failure/commit behavior without a registry or Linux build."""

import gzip
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fetch_io_vm", ROOT / "scripts/fetch-io-vm.py")
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name).resolve() / "packages"
        self.blobs = {"Image": bytes(56) + b"ARM\x64" + bytes(4),
                      "initramfs.cpio.gz": gzip.compress(b"070701fixture", mtime=0),
                      "kernel.config": b"configuration", "sources.tar.xz": b"source material"}
        self.manifest = {"schemaVersion": 2, "artifactType": FETCH.ARTIFACT_TYPE,
                         "annotations": {"org.hyper.architecture": "aarch64",
                                         "org.hyper.platform": "qemu"}, "layers": []}
        self.refresh()
        self.calls = []

    def refresh(self):
        self.manifest["layers"] = [
            {"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data),
             "annotations": {"org.opencontainers.image.title": name}}
            for name, data in self.blobs.items()]
        self.pin()

    def pin(self):
        self.raw = json.dumps(self.manifest).encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.reference = "ghcr.io/example/io-vm@sha256:" + self.digest

    def registry(self, command, **kwargs):
        self.calls.append(command)
        output = Path(command[command.index("--output") + 1])
        if command[1] == "manifest":
            output.write_bytes(self.raw)
        else:
            descriptor = command[-1].split("@", 1)[1]
            data = next(value for value in self.blobs.values()
                        if "sha256:" + hashlib.sha256(value).hexdigest() == descriptor)
            output.write_bytes(data)
        return subprocess.CompletedProcess(command, 0)

    def fetch(self):
        return FETCH.fetch(self.reference, "qemu", self.output)

    def test_verified_cache_is_offline_and_sources_are_not_fetched(self):
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            result = self.fetch()
            self.assertEqual(self.fetch(), result)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual({p.name for p in result.iterdir()},
                         {"Image", "initramfs.cpio.gz", "oci-manifest.json"})

    def test_tags_rejected_without_registry_access(self):
        with patch.object(FETCH.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                FETCH.fetch("ghcr.io/example/io-vm:latest", "qemu", self.output)
            run.assert_not_called()

    def test_lock_selects_only_an_immutable_qualified_platform(self):
        lock = Path(self.temporary.name) / "lock.json"
        metadata = {"version": 1, "platforms": {"qemu": {"reference": self.reference}}}
        lock.write_text(json.dumps(metadata))
        self.assertEqual(FETCH.pinned_reference("qemu", lock), self.reference)
        with self.assertRaises(ValueError):
            FETCH.pinned_reference("rpi5", lock)
        metadata["platforms"]["qemu"]["reference"] = "ghcr.io/example/io-vm:latest"
        lock.write_text(json.dumps(metadata))
        with self.assertRaises(ValueError):
            FETCH.pinned_reference("qemu", lock)

    def test_failed_download_keeps_previous_generation(self):
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            previous = self.fetch()
        self.blobs["Image"] += b"new generation"
        self.refresh()
        with patch.object(FETCH.subprocess, "run", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.fetch()
        self.assertEqual(list(self.output.iterdir()), [previous])

    def test_tampered_cached_image_is_rejected(self):
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            result = self.fetch()
        (result / "Image").write_bytes(bytes(64))
        with self.assertRaises(ValueError):
            self.fetch()

    def test_wrong_platform_missing_sources_and_traversal_rejected(self):
        for change in ("platform", "source", "path"):
            with self.subTest(change=change):
                self.refresh()
                self.manifest["annotations"]["org.hyper.platform"] = "qemu"
                if change == "platform":
                    self.manifest["annotations"]["org.hyper.platform"] = "rpi5"
                elif change == "source":
                    self.manifest["layers"].pop()
                else:
                    self.manifest["layers"][0]["annotations"]["org.opencontainers.image.title"] = "../Image"
                self.pin()
                with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
                    with self.assertRaises(ValueError):
                        self.fetch()
                self.assertFalse(any(self.output.iterdir()))

    def test_manifest_digest_must_match_the_pin(self):
        self.raw += b" "
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            with self.assertRaises(ValueError):
                self.fetch()
        self.assertFalse(any(self.output.iterdir()))

    def test_expanded_initramfs_budget_enforced(self):
        self.blobs["initramfs.cpio.gz"] = gzip.compress(bytes(32 * FETCH.MIB + 1))
        self.refresh()
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            with self.assertRaises(ValueError):
                self.fetch()
        self.assertFalse(any(self.output.iterdir()))


if __name__ == "__main__":
    unittest.main()
