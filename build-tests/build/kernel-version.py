#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Kernel build provenance for clean, dirty, archived and linked worktrees."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_version", ROOT / "kernel/tools/build-version.py")
VERSION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSION)


class Version(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Build test")
        self.git("config", "user.email", "build@example.invalid")
        (self.root / ".gitignore").write_text("target/\n")
        (self.root / "source").write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial source")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def metadata(self, root=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "0"}):
            VERSION.cargo_metadata(root or self.root)
        return output.getvalue()

    def test_clean_dirty_staged_and_untracked_sources(self):
        revision = self.git("rev-parse", "HEAD")
        self.assertEqual(VERSION.source_state(self.root), (revision, b""))
        self.assertIn(f"HYPER_BUILD_REVISION={revision[:12]}\n", self.metadata())
        (self.root / "target").mkdir()
        (self.root / "target/output").write_text("ignored")
        self.assertNotIn("-dirty", self.metadata())
        (self.root / "source").write_text("modified\n")
        self.assertIn(f"HYPER_BUILD_REVISION={revision[:12]}-dirty", self.metadata())
        self.git("add", "source")
        self.assertIn("-dirty", self.metadata())
        self.git("commit", "-qm", "Update source")
        self.assertNotIn("-dirty", self.metadata())
        (self.root / "new-source").write_text("new")
        self.assertIn("-dirty", self.metadata())
        self.assertIn(f"cargo:rerun-if-changed={self.root / 'new-source'}", self.metadata())
        self.assertNotIn(f"cargo:rerun-if-changed={self.root / 'target'}", self.metadata())

    def test_linked_worktree_uses_its_own_head_and_index(self):
        linked = self.root / "target/worktree"
        self.git("worktree", "add", "-qb", "version-test", str(linked))
        output = self.metadata(linked)
        head = VERSION.git(linked, "rev-parse", "--path-format=absolute", "--git-path", "HEAD").decode().strip()
        self.assertIn(f"cargo:rerun-if-changed={head}", output)
        self.assertNotIn("-dirty", output)

    def test_archive_is_explicitly_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIn("HYPER_BUILD_REVISION=unknown\n", self.metadata(Path(directory)))

    def test_reproducible_timestamp_and_invalid_input(self):
        self.assertIn("HYPER_BUILD_TIMESTAMP=1970-01-01T00:00:00Z", self.metadata())
        for value in ("", "-1", "invalid", "1.2", "١"):
            with self.subTest(value=value), patch.dict(os.environ, {"SOURCE_DATE_EPOCH": value}):
                with self.assertRaises(ValueError):
                    VERSION.build_timestamp()


if __name__ == "__main__":
    unittest.main()
