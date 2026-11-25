"""Integration test entry using an explicitly declared fixed uv executable."""

load("@rules_python//python:py_test.bzl", "py_test")

def runtime_lock_update_test(name, uv, uv_version = "0.12.18", **kwargs):
    """Run real resolver tests using a Bazel-managed uv artifact.

    Args:
        name: Test target name.
        uv: Label of a fixed uv executable for the execution host.
        uv_version: Reviewed version expected from that executable.
        **kwargs: Common py_test attributes.
    """
    py_test(
        name = name,
        srcs = [Label(":runtime_lock_update_test.py")],
        main = Label(":runtime_lock_update_test.py"),
        args = ["--uv", "$(rootpath %s)" % uv, "--uv-version", uv_version],
        data = [uv],
        deps = [Label("//python:runtime_lock_lib"), Label(":runtime_lock_test_support")],
        python_version = "3.11",
        **kwargs
    )
