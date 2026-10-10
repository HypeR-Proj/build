<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Native component builds

The root Makefile is the developer entry point. Cargo owns Rust dependency
resolution, CMake owns native compilation, and the component registry owns
delivery metadata. `make app-manifest` prints the expanded registry without
building an SDK or changing any files.

Each ordinary application and shared library declares delivery in its own
`component.mk`. `mk/components.mk` discovers `app/*/component.mk` and
`lib/*/component.mk`, then applies the shared templates under `mk/components/`.
Acceptance-only payloads are declared in `tests/native/component.mk`.

```make
# source directory, Cargo binary, staged filename, destination, image profiles
$(eval $(call native-rust-app,app/echo,hyper-echo,echo,bin/echo,system development))

# source directory, library filename; the template supplies the library layout
$(eval $(call native-rust-shared,lib/clap,libhyper_clap_shared.so))
```

The registry checks each declaration against its Cargo manifest and workspace
membership. Cargo package names are read from those manifests. Dependencies,
features and crate types remain in Cargo; the Make templates do not duplicate
them. Native-only dylib declarations also supply the host-test exclusion list.
Applications under `app/*/` are discovered by Cargo's member glob; `.cargo/`
and `target/` are excluded. Add a new app's `Cargo.toml` and `component.mk` in
its directory without editing a central member list. Libraries outside `app/`
remain explicit workspace members. There is no separately maintained deployment
JSON or test library list.

Each package owns its dependencies and features. Keep dependencies used by only
one app in that app's manifest (for example, shell's `shlex` and grep's `regex`).
The workspace supplies shared dependency versions, package metadata, lints and
build profiles. One `app/Cargo.lock` pins the delivery graph for applications and
Rust DSOs; a source-only app change does not require editing it. Dependency
changes should update only the relevant lock entries, without unrelated upgrades.

`native-file` registers an explicit SDK, configuration or fixture file, with
source, destination, mode and profiles. Sources can use `{apps}`, `{sdk}`,
`{std}` and `{arch}` placeholders. `native-symlink` takes target, destination
and profiles. These declarations are repository-controlled Make source, not an
input format for untrusted manifests.

## Build and composition

`make app` builds the selected ordinary binaries and workspace libraries in a
single Cargo invocation, then stages the coherent application/library set.
The staging directory's `build-manifest.json` records the source revision,
compiler, SDK fingerprints, features and exact artifact hashes and sizes.
Implementation rlibs remain usable by host tests. Native delivery crates select
their shared dependencies explicitly, including the single Rust std owner.
The optional probe binaries are selected with `APP_EXTRA_BINS` and installed
with their `hyper-` prefix removed.

`APP_FEATURES` and `APP_EXTRA_BINS` automatically select a deterministic variant
subdirectory under both `target/app/<arch>/` and `target/app-cargo/<arch>/`.
The empty/default configuration retains the ordinary output paths. Feature and
binary ordering does not affect variant identity. Explicit `APP_OUTPUT` and
`APP_CARGO_OUTPUT` overrides remain available for external test orchestration.
Architecture is part of the path; the SDK's link/std fingerprints and Rust
compiler configuration remain Cargo inputs. Do not mix artifacts from separate
Rust feature graphs, SDKs or compiler builds.

Image composition first applies profile membership and executable overrides.
Rust shared libraries are providers: the packer follows each final ELF's
`DT_NEEDED` and interpreter to add only its transitive dependencies. Providers
come from the staged applications and the selected SDK. Standalone fixtures use
`--library-dir` explicitly. Missing or ambiguous providers fail; host system
libraries are never used as a fallback. Libraries opened through `dlopen` must
be explicit `native-file` entries because they do not appear in `DT_NEEDED`.

The final payload is checked for architecture, SONAME and symbol closure before
publication. Existing symbol policy is preserved: release build artifacts retain
debug information, and packaging runs only `--strip-debug` on temporary copies.
The original ELF files, ordinary symbols and dynamic symbols are retained.

## SDK component cache

The SDK builder uses one CMake invocation template for libhyper, dynamic-loader
and userspace-loader. Their installed outputs are cached independently beside
the SDK in `<sdk-output>.components/`, keyed by source/dependency contents,
compiler/tool identities, configuration and relevant environment. Cache hits
verify output contents before restoring them into a fresh staging SDK.

This cache does not publish SDKs. The existing SDK publisher continues to
serialize writers, check inputs again, preserve unchanged timestamps and replace
the installed SDK only after the entire assembly succeeds. A Rust-source or SDK
version change can therefore reuse unchanged native components. Removing the
cache affects build time only.
