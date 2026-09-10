#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for content caches and failure-safe publication."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "sdk/toolchain/scripts/sysroot-state.py"
PACK = ROOT / "scripts/pack-native-initramfs.py"
spec = importlib.util.spec_from_file_location("state", STATE)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_state(self, *arguments):
        return subprocess.run([sys.executable, str(STATE), *map(str, arguments)], check=False).returncode

    def test_sdk_integrity_and_inputs(self):
        output = self.root / "sdk"
        output.mkdir()
        library = output / "lib.a"
        library.write_bytes(b"original")
        requested = self.root / "inputs.json"
        requested.write_text('{"options":"original"}')
        cached = Path(str(output) + ".build-state.json")
        self.assertEqual(self.run_state("record", output, requested, cached), 0)
        self.assertEqual(self.run_state("check", output, requested), 0)
        timestamp = library.stat().st_mtime_ns
        library.write_bytes(b"modified")
        os.utime(library, ns=(timestamp, timestamp))
        self.assertEqual(self.run_state("check", output, requested), 1)
        library.write_bytes(b"original")
        self.assertEqual(self.run_state("check", output, requested), 0)
        library.unlink()
        self.assertEqual(self.run_state("check", output, requested), 1)
        library.write_bytes(b"original")
        requested.write_text('{"options":"changed"}')
        self.assertEqual(self.run_state("check", output, requested), 1)

    def test_source_timestamps_and_native_link_identity(self):
        old, new = self.root / "old", self.root / "new"
        for path in (old, new):
            (path / "lib").mkdir(parents=True)
            (path / "share/hyper").mkdir(parents=True)
            (path / "lib/a").write_bytes(b"unchanged")
        timestamp = 1_000_000_000
        os.utime(old / "lib/a", ns=(timestamp, timestamp))
        state.preserve_times(old, new)
        self.assertEqual((new / "lib/a").stat().st_mtime_ns, timestamp)
        requested = self.root / "inputs.json"
        requested.write_text(json.dumps({"tools": {}, "environment": {}}))
        self.assertEqual(self.run_state("link-id", new, requested), 0)
        identity = (new / "share/hyper/link-fingerprint").read_bytes()
        (new / "lib/a").write_bytes(b"new native implementation")
        self.assertEqual(self.run_state("link-id", new, requested), 0)
        self.assertNotEqual((new / "share/hyper/link-fingerprint").read_bytes(), identity)
        state.preserve_times(old, new)
        self.assertNotEqual((new / "lib/a").stat().st_mtime_ns, timestamp)

    def test_std_source_identity_ignores_timestamps_but_tracks_content(self):
        output = self.root / "sdk"
        sources = output / "share/hyper/rust-src/library/std/src"
        sources.mkdir(parents=True)
        source = sources / "lib.rs"
        source.write_text("original")
        requested = self.root / "inputs.json"
        requested.write_text(json.dumps({"tools": {}, "environment": {}}))

        def identities():
            self.assertEqual(self.run_state("link-id", output, requested), 0)
            return tuple((output / "share/hyper" / name).read_bytes()
                         for name in ("link-fingerprint", "std-fingerprint"))

        original = identities()
        timestamp = source.stat().st_mtime_ns
        os.utime(source, ns=(timestamp + 1000, timestamp + 1000))
        self.assertEqual(identities(), original)
        source.write_text("changed std interface")
        os.utime(source, ns=(timestamp, timestamp))
        changed = identities()
        self.assertEqual(changed[0], original[0])
        self.assertNotEqual(changed[1], original[1])
        source.unlink()
        self.assertNotEqual(identities()[1], changed[1])

    def test_archive_cache_and_failed_strip(self):
        packer = self.root / "packer"
        packer.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nfor i in range(1, len(sys.argv), 3):\n sys.stdout.buffer.write(sys.argv[i].encode()+sys.argv[i+1].encode()+Path(sys.argv[i+2]).read_bytes())\n')
        packer.chmod(0o755)
        strip = self.root / "strip"
        strip.write_text('#!/bin/sh\nexit 0\n')
        strip.chmod(0o755)
        source = self.root / "app"
        source.write_bytes(b"\x7fELFpayload")
        original = source.read_bytes()
        output = self.root / "archive"
        command = [sys.executable, str(PACK), "--packer", str(packer), "--strip", str(strip), "--output", str(output), "0755", "bin/app", str(source)]

        def run(success=True):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            return result.stdout

        run()
        timestamp = output.stat().st_mtime_ns
        self.assertIn("up to date", run())
        self.assertEqual(output.stat().st_mtime_ns, timestamp)
        old_source_time = source.stat().st_mtime_ns
        source.write_bytes(b"\x7fELFchanged")
        os.utime(source, ns=(old_source_time, old_source_time))
        self.assertNotIn("up to date", run())
        self.assertIn(b"changed", output.read_bytes())
        output.write_bytes(b"corrupt")
        self.assertNotIn("up to date", run())
        output.unlink()
        run()
        previous = output.read_bytes()
        strip.write_text('#!/bin/sh\nexit 1\n')
        run(success=False)
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(source.read_bytes(), b"\x7fELFchanged")
        strip.write_text('#!/bin/sh\nexit 0\n')
        command[-3] = "0644"
        self.assertNotIn("up to date", run())
        source.write_bytes(original)
        run()
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
