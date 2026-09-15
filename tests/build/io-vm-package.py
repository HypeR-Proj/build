#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Package import failure/commit behavior without a registry or Linux build."""

import gzip
import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
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
        return FETCH.fetch(self.reference, "qemu", self.output, "oras")

    def test_verified_cache_is_offline_and_sources_are_not_fetched(self):
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            result = self.fetch()
            with patch.object(FETCH, "ensure_oras") as install:
                self.assertEqual(FETCH.fetch(self.reference, "qemu", self.output), result)
                install.assert_not_called()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual({p.name for p in result.iterdir()},
                         {"Image", "initramfs.cpio.gz", "oci-manifest.json"})

    def test_same_common_digest_imports_for_both_boards(self):
        self.manifest['annotations']['org.hyper.supported-platforms'] = '["qemu","rpi5"]'
        self.pin()
        with patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
            qemu = self.fetch()
            pi = FETCH.fetch(self.reference, 'rpi5', self.output)
        self.assertEqual(pi, qemu)
        self.assertEqual(len(self.calls), 3)

    def test_malformed_common_capabilities_are_rejected(self):
        for capabilities in ('[]', '["rpi5"]', '["qemu","qemu"]',
                             '["qemu","unknown"]', '"qemu"', '{}', 'null'):
            with self.subTest(capabilities=capabilities):
                self.manifest['annotations']['org.hyper.supported-platforms'] = capabilities
                self.pin()
                with patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
                    with self.assertRaises(ValueError):
                        self.fetch()

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


class OrasTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        patches = [
            patch.object(FETCH, "__file__", str(self.root / "scripts/fetch-io-vm.py")),
            patch.object(FETCH.shutil, "which", return_value=None),
            patch.object(FETCH.host_platform, "system", return_value="Darwin"),
            patch.object(FETCH.host_platform, "machine", return_value="arm64"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, command, **_kwargs):
        Path(command[command.index("--output") + 1]).write_bytes(self.archive)

    def archive_with(self, symbolic=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            member = tarfile.TarInfo("oras")
            content = b"#!/bin/sh\nexit 0\n"
            member.size = len(content)
            if symbolic:
                member.type = tarfile.SYMTYPE
                member.linkname = "/tmp/elsewhere"
            archive.addfile(member, io.BytesIO(content))
        self.archive = stream.getvalue()
        return hashlib.sha256(self.archive).hexdigest()

    def test_install_and_offline_reuse(self):
        checksum = self.archive_with()
        with patch.dict(FETCH.ORAS_SHA256, darwin_arm64=checksum), \
                patch.object(FETCH.subprocess, "run", side_effect=self.download) as download:
            result = Path(FETCH.ensure_oras())
            self.assertTrue(result.stat().st_mode & 0o111)
            self.assertEqual(str(result), FETCH.ensure_oras())
            download.assert_called_once()

    def test_bad_checksum_does_not_publish(self):
        self.archive_with()
        with patch.object(FETCH.subprocess, "run", side_effect=self.download):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                FETCH.ensure_oras()
        self.assertEqual(list(self.root.rglob("oras")), [])

    def test_archive_link_is_rejected(self):
        checksum = self.archive_with(symbolic=True)
        with patch.dict(FETCH.ORAS_SHA256, darwin_arm64=checksum), \
                patch.object(FETCH.subprocess, "run", side_effect=self.download):
            with self.assertRaisesRegex(ValueError, "invalid ORAS executable"):
                FETCH.ensure_oras()
        self.assertEqual(list(self.root.rglob("oras")), [])

    def test_path_tool_wins_without_download(self):
        with patch.object(FETCH.shutil, "which", return_value="/tools/oras"), \
                patch.object(FETCH.subprocess, "run") as download:
            self.assertEqual(FETCH.ensure_oras(), "/tools/oras")
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
