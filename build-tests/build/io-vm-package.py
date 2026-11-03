#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
"""Package import failure/commit behavior without a registry or Linux build."""

import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fetch_io_vm", ROOT / "scripts/fetch-io-vm.py")
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)
SPEC = importlib.util.spec_from_file_location("io_images", ROOT / "scripts/io-vm-images.py")
IMAGES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGES)


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

    def local_fixture(self, *, nested=False):
        local = self.output.parent / 'local'
        output = local / 'qemu' if nested else local
        package = output / 'boot/generation'
        package.mkdir(parents=True, exist_ok=True)
        names = {}
        for field, name, suffix in (('kernel', 'Image', ''), ('initramfs', 'initramfs.cpio.gz', '.cpio.gz')):
            data = self.blobs[name]
            filename = ('Image' if field == 'kernel' else 'initramfs') + '-' + hashlib.sha256(data).hexdigest() + suffix
            (package / filename).write_bytes(data)
            names[field] = filename
        (package / 'boot-artifacts.json').write_text(json.dumps(
            {'format': 1, 'architecture': 'aarch64', 'platform': 'qemu',
             'client_table_version': 5, **names}))
        (output / 'qemu-boot.json').write_text(json.dumps({'directory': str(package)}))
        return local, package

    def test_valid_local_build_precedes_registry_and_tool_installation(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                local, package = self.local_fixture(nested=nested)
                if nested:
                    (local / 'qemu-boot.json').unlink()
                with patch.object(FETCH, 'ensure_oras') as install, \
                        patch.object(FETCH, 'qualified_reference') as resolve, \
                        patch.object(FETCH, 'fetch') as fetch:
                    self.assertEqual(FETCH.select_package(None, 'qemu', self.output, local), package)
                    install.assert_not_called()
                    resolve.assert_not_called()
                    fetch.assert_not_called()

    def test_default_cli_uses_local_build_without_attempting_network_access(self):
        local, package = self.local_fixture()
        output = io.StringIO()
        with patch.dict(os.environ, HYPER_BUILD_VIEW='1', IO_VM_LOCAL_BUILD=str(local)), \
                patch.object(sys, 'argv', ['fetch-io-vm.py', '--platform', 'qemu', '--output', str(self.output)]), \
                patch.object(sys, 'stdout', output), \
                patch.object(FETCH, 'ensure_oras', side_effect=AssertionError('network attempted despite valid local build')):
            FETCH.main()
        self.assertEqual(output.getvalue().strip(), str(package))

    def test_invalid_local_build_falls_back_to_a_verified_remote_generation(self):
        for change in ('platform', 'architecture', 'protocol', 'bytes', 'missing', 'escape', 'metadata'):
            with self.subTest(change=change):
                local, package = self.local_fixture()
                path = package / 'boot-artifacts.json'
                metadata = json.loads(path.read_text())
                if change in ('platform', 'architecture'):
                    metadata[change] = 'wrong'
                elif change == 'protocol':
                    metadata['client_table_version'] = 4
                elif change == 'bytes':
                    (package / metadata['kernel']).write_bytes(b'corrupt')
                elif change == 'missing':
                    (package / metadata['initramfs']).unlink()
                elif change == 'escape':
                    (local / 'qemu-boot.json').write_text(json.dumps({'directory': str(local.parent)}))
                else:
                    metadata = None
                path.write_text(json.dumps(metadata))
                with patch.object(FETCH, 'qualified_reference', return_value=self.reference) as resolve, \
                        patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
                    selected = FETCH.select_package(None, 'qemu', self.output, local, 'oras')
                self.assertEqual(selected, self.output / self.digest)
                resolve.assert_called_once_with('qemu', 'oras')

    def test_explicit_digest_and_materials_do_not_select_an_unrelated_local_build(self):
        local, _ = self.local_fixture()
        with patch.object(FETCH, 'local_build') as inspect, \
                patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
            selected = FETCH.select_package(self.reference, 'qemu', self.output, local, 'oras')
            inspect.assert_not_called()
        self.assertEqual(selected, self.output / self.digest)
        with patch.object(FETCH, 'local_build') as inspect, \
                patch.object(FETCH, 'qualified_reference', return_value=self.reference), \
                patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
            selected = FETCH.select_package(None, 'qemu', self.output, local, 'oras', materials=True)
            inspect.assert_not_called()
        self.assertEqual((selected / 'materials/sources.tar.xz').read_bytes(), self.blobs['sources.tar.xz'])

    def test_unavailable_local_and_remote_print_explicit_build_command_without_building(self):
        error = io.StringIO()
        with patch.dict(os.environ, HYPER_BUILD_VIEW='1', HYPER_BUILD_OUTPUT=str(self.output.parent)), \
                patch.object(sys, 'argv', ['fetch-io-vm.py', '--platform', 'rpi5', '--oras', 'oras',
                                         '--local-build', str(self.output.parent / 'absent')]), \
                patch.object(sys, 'stderr', error), \
                patch.object(FETCH, 'qualified_reference', side_effect=OSError('registry unavailable')), \
                patch.object(FETCH.subprocess, 'run') as run, self.assertRaises(SystemExit) as failure:
            FETCH.main()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn('registry unavailable', error.getvalue())
        self.assertIn('make iovm IO_VM_PLATFORM=rpi5', error.getvalue())
        self.assertIn('Linux host', error.getvalue())
        run.assert_not_called()

    def test_iovm_target_delegates_only_explicit_build_to_appliance_owner(self):
        root = self.output.parent
        makefile = root / 'io-vm/mk/build.mk'
        makefile.parent.mkdir(parents=True)
        makefile.write_text('base:\n\t@test "$(PLATFORM)" = rpi5\n'
                            '\t@test "$(OUTPUT)" = "$(EXPECTED)"\n'
                            '\t@touch built\n')
        for name in ('custom', 'custom output'):
            with self.subTest(name=name):
                result = subprocess.run(['make', '-f', str(ROOT / 'mk/boards.mk'), 'iovm',
                                         'IO_VM_PLATFORM=rpi5', f'IO_VM_LOCAL_BUILD={name}',
                                         f'EXPECTED={root}/{name}'], cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((root / 'io-vm/built').is_file())
                (root / 'io-vm/built').unlink()

    def test_verified_cache_is_offline_and_sources_are_not_fetched(self):
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            result = self.fetch()
            with patch.object(FETCH, "ensure_oras") as install:
                self.assertEqual(FETCH.fetch(self.reference, "qemu", self.output), result)
                install.assert_not_called()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual({p.name for p in result.iterdir()},
                         {"Image", "initramfs.cpio.gz", "oci-manifest.json"})

    def test_corresponding_sources_share_runtime_digest_and_recheck_cached_bytes(self):
        with patch.object(FETCH.subprocess, "run", side_effect=self.registry):
            directory = self.fetch()
            materials = FETCH.fetch_materials(self.reference, "qemu", directory, "oras")
            self.assertEqual((materials / "sources.tar.xz").read_bytes(), self.blobs["sources.tar.xz"])
            calls = len(self.calls)
            FETCH.fetch_materials(self.reference, "qemu", directory, "oras")
            self.assertEqual(len(self.calls), calls)
            (materials / "sources.tar.xz").write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                FETCH.fetch_materials(self.reference, "qemu", directory, "oras")

    def test_same_common_digest_imports_for_both_boards(self):
        self.manifest['annotations']['org.hyper.supported-platforms'] = '["qemu","rpi5"]'
        self.pin()
        with patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
            qemu = self.fetch()
            pi = FETCH.fetch(self.reference, 'rpi5', self.output)
        self.assertEqual(pi, qemu)
        self.assertEqual(len(self.calls), 3)

    def test_storage_deployment_requires_appliance_resource_support(self):
        for version in (None, '3', '4', '5'):
            with self.subTest(version=version):
                if version is not None:
                    self.manifest['annotations']['org.hyper.client-table-version'] = version
                self.pin()
                with patch.object(FETCH.subprocess, 'run', side_effect=self.registry):
                    package = self.fetch()
                if version == '5':
                    self.assertEqual(IMAGES.package_payloads(package, require_client_resources=True),
                                     (package / 'Image', package / 'initramfs.cpio.gz'))
                else:
                    with self.assertRaisesRegex(ValueError, 'hyper.clients.v5'):
                        IMAGES.package_payloads(package, require_client_resources=True)

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

    def test_qualified_channel_resolves_once_to_an_immutable_generation(self):
        for platform in ("qemu", "rpi5"):
            with self.subTest(platform=platform):
                with patch.object(FETCH.subprocess, "check_output", return_value="sha256:" + self.digest + "\n") as resolve:
                    reference = FETCH.qualified_reference(platform, "oras")
                resolve.assert_called_once_with(
                    ["oras", "resolve", f"ghcr.io/hyper-proj/io-vm:main-aarch64-{platform}"], text=True)
                self.assertEqual(reference, FETCH.REGISTRY + "@sha256:" + self.digest)
        for response in ("latest", "sha256:bad", "sha256:" + self.digest + "\nsha256:" + self.digest):
            with patch.object(FETCH.subprocess, "check_output", return_value=response):
                with self.assertRaises(ValueError):
                    FETCH.qualified_reference("qemu", "oras")

    def test_unavailable_channel_does_not_silently_fall_back(self):
        with patch.object(FETCH.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "oras")):
            with self.assertRaises(subprocess.CalledProcessError):
                FETCH.qualified_reference("qemu", "oras")

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
