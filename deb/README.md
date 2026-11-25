# Source changelog installation

`debian_changelogs` runs the locked APT `dh_installchangelogs` helper and
exposes its selected installed files before final compression. It preserves
the original trimming, native package naming, upstream history and Debian NEWS
selection. It does not assemble a Debian package.

Declare the original metadata and helper arguments. Each output basename must
be the filename installed by the original helper. Reference individual output
labels when passing a file to another rule:

```starlark
load("@sonic_build_infra//deb:changelogs.bzl", "debian_changelogs")

debian_changelogs(
    name = "installed_changelog",
    control = "@ifupdown2_src//:debian/control",
    changelog = "@ifupdown2_src//:debian/changelog",
    debian_files = {"@ifupdown2_src//:debian/compat": "compat"},
    package = "ifupdown2",
    architecture = "amd64",
    outs = ["generated/changelog.Debian"],
)
```

The example is the AMD64 ifupdown2 packaging contract. Pass the component's
selected Debian architecture. A native package installs `changelog`; a
non-native package installs `changelog.Debian`. Supply `upstream` when the
original helper selects an upstream history file, and declare its `changelog`
output separately. Additional `debian_files` map source labels to paths such as
`NEWS` or `sensord.NEWS` inside the helper's private Debian metadata directory.
Only declare a NEWS output when the original helper actually installs it;
trimming can remove all of its entries.

Feed these raw outputs to the existing pigz and installation TAR rules.
`helper_args` carries original explicit options such as `--no-trim`; do not add
that option to avoid the package's normal trimming behavior. The rule stages
only helper metadata and consumes the upstream file directly. It does not copy
the component source tree.
