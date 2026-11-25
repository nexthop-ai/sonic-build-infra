# Wheel installation and runtime locks

`wheel_layer` installs source-owner and pinned external wheels with PyPA installer.
It preserves wheel metadata, resources and entry points, and emits one reproducible
TAR as `DefaultInfo`. It does not resolve dependencies or run package code.

The rule also emits `PythonRuntimeInfo` and the `metadata`/`runtime_tars` output
groups. Each selected wheel supplies its own runtime JSON from the exact METADATA
and WHEEL files installed in that TAR. Records retain distribution, version,
Requires-Python, Requires-Dist including markers, declared extras, wheel tags and
the SHA256 of the wheel and its METADATA. Filename name/version/tags must agree
with the wheel metadata; PyPA installer checks the complete RECORD before install.
The owning wheel label becomes the producer identity in a runtime plan.

```starlark
load("@sonic_build_infra//python:wheel_layer.bzl", "wheel_layer")
load("@sonic_build_infra//python:runtime_lock.bzl", "python_runtime_plan", "checked_python_runtime")
load("@image_pip//:requirements.bzl", "all_whl_requirements")

wheel_layer(
    name = "owner_python",
    wheels = ["@service//:service_wheel", "@common//:common_wheel"],
    site_packages = "/usr/local/lib/python3.13/dist-packages",
)
python_runtime_plan(
    name = "runtime_plan",
    packages = [":owner_python"],
    roots = ["service[metrics]"],
    source_distributions = ["service", "common"],
    site_packages = ["/usr/local/lib/python3.13/dist-packages"],
    python_version = "3.13",
    python_platform = "x86_64-unknown-linux-gnu",
    uv_version = "0.12.18",
)
wheel_layer(
    name = "external_python",
    wheels = all_whl_requirements,
    site_packages = "/usr/local/lib/python3.13/dist-packages",
)
checked_python_runtime(
    name = "runtime",
    plan = ":runtime_plan",
    requirements = "requirements.in",
    lock = "requirements_lock.txt",
    inputs = "lock-inputs.json",
    sources = [":owner_python"],
    third_party = [":external_python"],
    update_command = "tools/update-image-python-lock",
)
```

Use actual package names, owner update command and target paths. Generate the
reviewed requirements, lock and input record with the existing `runtime_lock`
updater, then install only `:runtime` into the image. Keep owner and external
wheels in separate layers: the checker verifies the complete source set and
external locked set separately and rejects duplicates or substitutions. Installed
metadata must match the source plan; missing transitives and stale locks fail.
The exact selected owner wheel identity participates in the plan digest, so a
changed wheel requires a reviewed lock-input update even when dependencies stay
unchanged. Ordinary builds never resolve dependencies or rewrite locks.

Optional `runtime_deps` collects other source `PythonRuntimeInfo` providers without
installing their files in this layer. The provider contains the transitive TARs
and metadata; this target's `DefaultInfo` remains its own TAR. Inherited image
payloads can use `checked_python_runtime`'s `inherited_sources` and
`inherited_third_party` arguments once the consumer verifies those exact artifacts
are installed in the parent. Wheel tag consistency is not proof of target ABI or
runtime compatibility; those remain installed-image acceptance checks.

Provider package structs expose `producer`, `tar`, `metadata` and, for wheels,
`wheel`. Distribution and version are execution-time facts in the JSON; do not
assume static `distribution`/`version` fields for wheel producers. Existing direct
source producers retain their previous fields and can coexist in collectors.
