"""Expose an ELF-profile entry point with its complete tree as ordinary inputs."""

load("@bazel_lib//lib:paths.bzl", "relative_file")

def _elf_runtime_tool_impl(ctx):
    # Follow the executable configuration. Consumers such as tar_toolchain
    # already select this rule with cfg="exec". A second transition would
    # make relative output links disagree with their runfiles layout.
    runtime = ctx.file.runtime
    if not runtime.is_directory:
        fail("runtime must provide the prepared ELF TreeArtifact")
    if not ctx.attr.tool or "/" in ctx.attr.tool or ctx.attr.tool in [".", ".."]:
        fail("tool must name one executable in the ELF runtime's bin directory")
    executable = ctx.actions.declare_symlink(ctx.label.name)
    ctx.actions.symlink(
        output = executable,
        target_path = relative_file(runtime.path + "/bin/" + ctx.attr.tool, executable.path),
    )
    files = depset([executable, runtime])
    return [DefaultInfo(
        executable = executable,
        files = files,
        runfiles = ctx.runfiles(transitive_files = files),
    ), OutputGroupInfo(binary = depset([executable]), runtime = depset([runtime]))]

elf_runtime_tool = rule(
    implementation = _elf_runtime_tool_impl,
    executable = True,
    doc = "Export an existing self-contained ELF runtime entry without a Python/runfiles launcher. DefaultInfo.files explicitly carries the runtime for upstream toolchains that propagate files only.",
    attrs = {
        "runtime": attr.label(mandatory = True, allow_single_file = True),
        "tool": attr.string(mandatory = True),
    },
)
