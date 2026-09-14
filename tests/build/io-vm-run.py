#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Protect persistent disks used by interactive I/O VM boots."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'io_runner', Path(__file__).resolve().parents[2] / 'scripts/run-io-vm.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class DiskTests(unittest.TestCase):
    def test_create_and_reuse_does_not_truncate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disk.img'
            runner.prepare_disk(path, 8 * 1024 * 1024)
            with path.open('r+b') as stream:
                stream.write(b'existing volume')
            runner.prepare_disk(path, 64 * 1024 * 1024)
            self.assertEqual(path.stat().st_size, 8 * 1024 * 1024)
            with path.open('rb') as stream:
                self.assertEqual(stream.read(15), b'existing volume')

    def test_invalid_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disk.img'
            path.write_bytes(b'not a disk')
            with self.assertRaises(ValueError):
                runner.prepare_disk(path, 8 * 1024 * 1024)
            self.assertEqual(path.read_bytes(), b'not a disk')


if __name__ == '__main__':
    unittest.main()
