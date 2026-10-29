<!-- SPDX-FileCopyrightText: 2026 roolrz -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Incremental builds

Use the normal `make image`, `make app`, and `make native-initramfs` commands.
Keep `target/` and `kernel/target/` between builds. The first build after changing
the cache layout populates new Cargo directories; subsequent builds reuse them.
No special incremental-build flag is required.

## Dependency tracking

- Cargo owns Rust source, configuration, feature, and dependency tracking.
  Applications still compile against the installed SDK.
- SDK publication checks source contents, compiler/tool identities, build options,
  and the contents and modes of installed files. An unchanged SDK is reused.
  A changed SDK is prepared in isolation and published only after successful
  construction. Byte-identical installed files retain their previous timestamps,
  so an SDK-only edit does not invalidate every std and application crate.
- Native libraries, startup objects, linker scripts, and SDK linker tools contribute
  a link fingerprint to `hyper-cargo`'s compiler options. Changes to these inputs
  invalidate Cargo consumers even though they are outside Cargo's crate graph.
- Bare and embedded kallsyms passes use separate Cargo caches. The seed table is
  rewritten only when its contents change; the final ELF receives the exact
  post-link symbol table. ELF and image publication preserve unchanged files.
- Dynamic and static std smoke builds use separate Cargo target directories;
  Clippy has its own directory. Switching test modes no longer discards the
  previous mode's compilation cache.
- Application installation and FIT generation preserve identical outputs.
  Initramfs packing checks payload contents, archive paths and modes, tools, and
  the existing archive's digest. Only a changed or missing archive is repacked.
  ELF stripping still happens on staging copies, with original debug information
  and packaged symbol tables preserved.

SDK and initramfs cache validation reads file contents, rather than relying only
on mtimes. Timestamp-preserving changes and missing or corrupted cached outputs
therefore invalidate these caches. Inputs are checked again before publication;
if they change during construction, the build fails and should be retried.
Cargo retains its own upstream freshness rules for Rust compilation.

## Recovery and costs

Removing an SDK or initramfs output (or its adjacent `.build-state.json`) causes
reconstruction on the next invocation. Missing extracted guest images are
regenerated from the guest download cache. `make clean` removes both `target/`
and `kernel/target/`, including the default persistent board and diagnostic
disks, and requires a fresh build afterward. Keep disks outside these output
trees with `BOARD_IMAGE` or `IO_VM_DISK` if they must survive cleaning.

The additional kallsyms and std-mode Cargo directories trade disk space and a
larger first build for stable subsequent builds. Content validation and small
host-tool invocations still run on an unchanged build. SDK source changes may
rebuild its small C runtime, but unchanged installed Rust sources retain their
Cargo caches. This is not a remote build cache.

Do not run independent builds that publish the same output concurrently. SDK
publication rejects concurrent publishers; kernel architectures also share the
default `.config`. Build architectures serially, or use separate checkouts.

Run `python3 tests/build/incremental.py` for cache invalidation and failure-path
regressions. It is also included in `sh tests/ci/run.sh scripts`. SDK publication
rollback and publisher exclusion remain covered by `make sdk-check`.
