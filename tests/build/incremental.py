#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for content caches and failure-safe publication."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
STATE = ROOT / "sdk/toolchain/scripts/sysroot-state.py"
PACK = ROOT / "scripts/pack-native-initramfs.py"
spec = importlib.util.spec_from_file_location("state", STATE)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
pack_spec = importlib.util.spec_from_file_location("native_pack", PACK)
native_pack = importlib.util.module_from_spec(pack_spec)
pack_spec.loader.exec_module(native_pack)


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
        # Interpreter changes must still invalidate links after moving out of
        # the SDK's ordinary link-library directory.
        identity = (new / "share/hyper/link-fingerprint").read_bytes()
        (new / "lib64").mkdir()
        loader = new / "lib64/ld-hyper-aarch64.so"
        loader.write_bytes(b"interpreter")
        self.assertEqual(self.run_state("link-id", new, requested), 0)
        self.assertNotEqual((new / "share/hyper/link-fingerprint").read_bytes(), identity)
        identity = (new / "share/hyper/link-fingerprint").read_bytes()
        loader.write_bytes(b"changed interpreter")
        self.assertEqual(self.run_state("link-id", new, requested), 0)
        self.assertNotEqual((new / "share/hyper/link-fingerprint").read_bytes(), identity)

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
        # Minimal static ELF metadata; the stand-in strip tool leaves it intact.
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', header, 18, 183)
        struct.pack_into('<H', header, 54, 56)
        source.write_bytes(header + b"payload")
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
        source.write_bytes(header + b"changed")
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
        self.assertEqual(source.read_bytes(), header + b"changed")
        strip.write_text('#!/bin/sh\nexit 0\n')
        command[-3] = "0644"
        self.assertNotIn("up to date", run())
        source.write_bytes(original)
        run()
        self.assertEqual(source.read_bytes(), original)

    def test_entry_manifest_paths_cache_and_failed_publication(self):
        packer = self.root / 'packer'
        packer.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
                          'for i in range(1, len(sys.argv), 3):\n'
                          ' sys.stdout.buffer.write(sys.argv[i].encode()+sys.argv[i+1].encode()'
                          '+Path(sys.argv[i+2]).read_bytes())\n')
        packer.chmod(0o755)
        strip = self.root / 'strip'
        strip.write_text('#!/bin/sh\nexit 0\n')
        strip.chmod(0o755)
        source = self.root / "guest image $();'.itb"
        source.write_bytes(b'guest image')
        manifest = self.root / 'entries.json'
        entries = [['0644', "vm/guest image $();'.itb", str(source)]]
        manifest.write_text(json.dumps(entries))
        output = self.root / 'archive'
        command = [sys.executable, str(PACK), '--packer', str(packer), '--strip', str(strip),
                   '--output', str(output), '--entries-from', str(manifest)]

        def run(success=True):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            return result.stdout

        run()
        self.assertEqual(output.read_bytes(), b"0644vm/guest image $();'.itbguest image")
        self.assertIn('up to date', run())
        entries[0][1] = 'vm/renamed.itb'
        manifest.write_text(json.dumps(entries))
        self.assertNotIn('up to date', run())
        self.assertIn(b'vm/renamed.itb', output.read_bytes())
        source.write_bytes(b'changed image')
        self.assertNotIn('up to date', run())
        self.assertIn(b'changed image', output.read_bytes())
        previous = output.read_bytes()
        original_inputs = native_pack.inputs

        def changed_after_decode(args):
            manifest.write_text(json.dumps([['0644', 'vm/new-generation.itb', str(source)]]))
            return original_inputs(args)

        with patch.object(sys, 'argv', command[1:]), \
                patch.object(native_pack, 'inputs', side_effect=changed_after_decode), \
                self.assertRaisesRegex(RuntimeError, 'manifests changed while loading'):
            native_pack.main()
        self.assertEqual(output.read_bytes(), previous)
        for invalid in (entries + entries, [['0644', '../escape', str(source)]],
                        [['0644', 'vm/valid.itb', str(self.root / 'missing')]]):
            manifest.write_text(json.dumps(invalid))
            run(success=False)
            self.assertEqual(output.read_bytes(), previous)
        manifest.write_text(json.dumps(entries))
        command.extend(['0644', 'vm/renamed.itb', str(source)])
        run(success=False)
        self.assertEqual(output.read_bytes(), previous)

    def test_entry_manifest_rejects_invalid_shapes_and_archive_collisions(self):
        manifest = self.root / 'entries.json'
        for invalid in ({}, ['0644', 'vm/io.itb', '/host/io.itb'],
                        [['0644', 'vm/io.itb']], [[644, 'vm/io.itb', '/host/io.itb']],
                        [['0644', 'vm/io.itb', 'relative.itb']]):
            manifest.write_text(json.dumps(invalid))
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                native_pack.load_entries(manifest)
        for entries in (['0999', 'vm/io.itb', '/host/io.itb'],
                        ['0644', '/vm/io.itb', '/host/io.itb'],
                        ['0644', 'vm//io.itb', '/host/io.itb'],
                        ['0644', 'vm/./io.itb', '/host/io.itb'],
                        ['0644', 'vm/../io.itb', '/host/io.itb'],
                        ['symlink', 'lib', '/host/lib'],
                        ['symlink', 'lib', '../outside'],
                        ['symlink', 'lib', 'lib64//native'],
                        ['symlink', 'lib', 'lib64', '0755', 'lib/tool', '/host/tool'],
                        ['0644', 'vm/io.itb', 'bad\0source'],
                        ['0644', 'vm/io.itb', '/one', '0755', 'vm/io.itb', '/two'],
                        ['0644', 'vm/io.itb', '/one', '0644', 'vm', '/two']):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                native_pack.validate_entries(entries)

    def test_real_archive_preserves_symlink_type_target_and_cache_identity(self):
        packer = self.root / 'newc-pack'
        subprocess.run([shutil.which('clang') or 'cc', '-std=c17', '-Wall', '-Wextra',
                        '-Werror', str(ROOT / 'tools/newc-pack.c'), '-o', str(packer)], check=True)
        source = self.root / 'note'
        source.write_bytes(b'archive payload')
        output = self.root / 'archive.cpio'
        command = [sys.executable, str(PACK), '--packer', str(packer), '--strip', '/usr/bin/true',
                   '--output', str(output), 'symlink', 'lib', 'lib64',
                   '0644', 'lib64/aarch64-hyper-hyper/note', str(source)]
        subprocess.run(command, check=True, capture_output=True)
        data = output.read_bytes()
        entries = {}
        offset = 0
        while True:
            header = data[offset:offset + 110]
            self.assertEqual(header[:6], b'070701')
            mode = int(header[14:22], 16)
            size, namesize = int(header[54:62], 16), int(header[94:102], 16)
            name = data[offset + 110:offset + 110 + namesize - 1].decode()
            start = (offset + 110 + namesize + 3) & ~3
            if name == 'TRAILER!!!':
                break
            entries[name] = (mode, data[start:start + size])
            offset = (start + size + 3) & ~3
        self.assertEqual(entries['lib'], (stat.S_IFLNK | 0o777, b'lib64'))
        self.assertEqual(entries['lib64/aarch64-hyper-hyper/note'],
                         (stat.S_IFREG | 0o644, b'archive payload'))
        timestamp = output.stat().st_mtime_ns
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertIn('up to date', result.stdout)
        self.assertEqual(output.stat().st_mtime_ns, timestamp)
        # Link data is hashed without opening a same-named build-host path.
        command[command.index('lib64')] = 'alternate'
        subprocess.run(command, check=True, capture_output=True)
        self.assertNotEqual(output.read_bytes(), data)
        previous = output.read_bytes()
        command[command.index('alternate')] = '../escape'
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(output.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
