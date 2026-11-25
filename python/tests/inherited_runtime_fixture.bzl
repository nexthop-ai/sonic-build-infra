"""Real TAR and metadata fixtures for the OCI Python inheritance contract."""
load("@bazel_skylib//rules:write_file.bzl", "write_file")
load("@rules_python//python:py_binary.bzl", "py_binary")
load("@rules_python//python:py_test.bzl", "py_test")
load("//python:site_packages.bzl", "site_packages")
load("//python:runtime_lock.bzl", "checked_python_runtime", "python_runtime_plan")

def inherited_runtime_fixture():
    for target, distribution, version, requires in [
        ("parent", "parent", "1.0", []),
        ("wrong", "parent", "2.0", []),
        ("child", "child", "1.0", ["parent==1.0", "external==2.0"]),
        ("external", "external", "2.0", []),
    ]:
        write_file(name = "inherited_" + target + "_file", out = "inherited_" + target + ".py", content = ["VALUE = " + repr(target)])
        site_packages(name = "inherited_" + target, distribution = distribution, version = version,
            files = {":inherited_" + target + "_file": distribution + ".py"}, requires = requires,
            site_packages = "/opt/runtime/site", scripts = "/opt/runtime/bin", headers = "/opt/runtime/include",
            data_prefix = "/opt/runtime", interpreter = "/usr/bin/python3")
    python_runtime_plan(name = "inherited_plan", packages = [":inherited_parent", ":inherited_child"],
        site_packages = ["/opt/runtime/site"], roots = ["child"], source_distributions = ["parent", "child"], python_version = "3.13",
        python_platform = "x86_64-unknown-linux-gnu", uv_version = "0.11.0")
    py_binary(name = "inherited_lock_fixture", srcs = ["inherited_lock_fixture.py"], main = "inherited_lock_fixture.py", python_version = "3.11")
    native.genrule(name = "inherited_lock_inputs", srcs = [":inherited_plan"],
        outs = ["inherited.requirements.in", "inherited.requirements_lock.txt", "inherited.inputs.json"],
        tools = [":inherited_lock_fixture"], cmd = "$(location :inherited_lock_fixture) $< $(OUTS)")
    checked_python_runtime(name = "inherited_runtime", sources = [":inherited_child"],
        inherited_sources = [":inherited_parent"], inherited_third_party = [":inherited_external"],
        plan = ":inherited_plan", requirements = ":inherited.requirements.in", lock = ":inherited.requirements_lock.txt",
        inputs = ":inherited.inputs.json", update_command = "fixed inheritance test fixture")
    labels = {"runtime": ":inherited_runtime", "plan": ":inherited_plan", "lock": ":inherited.requirements_lock.txt",
        "child": ":inherited_child", "parent": ":inherited_parent", "wrong": ":inherited_wrong",
        "external": ":inherited_external", "unexpected": ":leaf"}
    py_test(name = "inherited_runtime_test", srcs = ["inherited_runtime_test.py"], main = "inherited_runtime_test.py",
        args = ["--" + key + "=$(location " + label + ")" for key, label in labels.items()], data = labels.values(),
        deps = ["//python:runtime_lock_lib"], python_version = "3.11")
