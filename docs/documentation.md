<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Repository documentation

Each repository owns its README and optional detailed guides. HypeR supplies the
system introduction, Quick Start and a catalog linking to the component sites.
Documentation changes belong in the same PR as the implementation they describe.

The Documentation workflow builds only the current repository. PRs validate local
links and source references; a push resulting from a merge to `main` publishes the
site through GitHub Pages. Documentation does not depend on system builds, I/O VM
compilation or Pi5 image generation.

A repository's entry point is `README.md`; additional tracked Markdown files are
published at their existing paths. Root README becomes `index.html`; other pages
use `.html` in place of `.md`. Relative links stay within that repository. Links
to tracked source files point to the owning GitHub repository and exact revision.
Cross-repository links must use an explicit documentation-site or GitHub URL.

Build a site locally from the workspace:

```sh
python3 -m venv /tmp/hyper-docs
/tmp/hyper-docs/bin/pip install -r scripts/docs/requirements.txt
/tmp/hyper-docs/bin/python scripts/docs/build.py \
  --root kernel --repository kernel --output /tmp/kernel-docs
```

Choose a fresh output directory. The builder checks generated HTML links and
anchors before the site is uploaded. The shared action is provided by `build`;
`ci` owns the reusable GitHub Actions workflow. Each repository controls when to
invoke it through its own `.github/workflows/documentation.yml`.
