"""Install raw changelog files using the declared Debian packaging helper."""

def _impl(ctx):
    outputs = {output.basename: output.path for output in ctx.outputs.outs}
    if len(outputs) != len(ctx.outputs.outs):
        fail("changelog outputs must have distinct installed basenames")
    files = {"control": ctx.file.control, "changelog": ctx.file.changelog}
    for target, destination in ctx.attr.debian_files.items():
        candidates = target[DefaultInfo].files.to_list()
        if len(candidates) != 1 or destination in files:
            fail("each Debian metadata path must have one distinct source")
        files[destination] = candidates[0]
    spec = ctx.actions.declare_file(ctx.label.name + ".inputs.json")
    ctx.actions.write(spec, json.encode({
        "debian_files": {name: file.path for name, file in files.items()},
        "package": ctx.attr.package,
        "architecture": ctx.attr.architecture,
        "runtime": ctx.file._runtime.path,
        "upstream": ctx.file.upstream.path if ctx.file.upstream else None,
        "helper_args": ctx.attr.helper_args,
        "outputs": outputs,
    }))
    ctx.actions.run(
        executable = ctx.attr._tool[DefaultInfo].files_to_run,
        arguments = [spec.path],
        inputs = depset([spec, ctx.file._runtime] + files.values() + ([ctx.file.upstream] if ctx.file.upstream else [])),
        outputs = ctx.outputs.outs,
        mnemonic = "DebianChangelogs",
    )
    return [DefaultInfo(files = depset(ctx.outputs.outs))]

debian_changelogs = rule(
    implementation = _impl,
    doc = "Expose selected raw files installed by dh_installchangelogs, before final compression. Output basenames are the original installed filenames.",
    attrs = {
        "outs": attr.output_list(mandatory = True),
        "control": attr.label(allow_single_file = True, mandatory = True),
        "changelog": attr.label(allow_single_file = True, mandatory = True),
        "debian_files": attr.label_keyed_string_dict(allow_files = True),
        "upstream": attr.label(allow_single_file = True),
        "package": attr.string(mandatory = True),
        "architecture": attr.string(mandatory = True),
        "helper_args": attr.string_list(),
        "_runtime": attr.label(default = Label("//tools/build_tools:kernel_runtime"), allow_single_file = True, cfg = "exec"),
        "_tool": attr.label(default = Label("//deb:changelogs_tool"), executable = True, cfg = "exec"),
    },
)
