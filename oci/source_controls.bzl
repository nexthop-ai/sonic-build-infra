"""Project one authoritative source package's generated DEBIAN control tree."""
load(":runtime_packages.bzl", "RuntimePackagesInfo")

def _impl(ctx):
    info = ctx.attr.metadata[RuntimePackagesInfo]
    output = ctx.actions.declare_file(ctx.label.name + ".tar")
    receipt = ctx.actions.declare_file(ctx.label.name + ".receipt.json")
    spec = ctx.actions.declare_file(ctx.label.name + ".inputs.json")
    ctx.actions.write(spec, json.encode({
        "metadata": info.metadata.path,
        "package": ctx.attr.package,
        "trees": [tree.path for tree in info.trees.to_list()],
    }))
    tool = ctx.attr._tool[DefaultInfo].files_to_run
    ctx.actions.run(
        executable = tool,
        arguments = [spec.path, output.path, receipt.path],
        inputs = depset([spec, info.metadata], transitive = [info.trees]),
        outputs = [output, receipt],
        mnemonic = "SourcePackageControls",
    )
    return [DefaultInfo(files = depset([output])), OutputGroupInfo(receipt = depset([receipt]))]

source_package_controls = rule(
    implementation = _impl,
    attrs = {
        "metadata": attr.label(mandatory = True, providers = [RuntimePackagesInfo]),
        "package": attr.string(mandatory = True),
        "_tool": attr.label(default = Label(":source_controls_tool"), executable = True, cfg = "exec"),
    },
)
