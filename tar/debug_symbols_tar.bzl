"""Package build-id symbols directly from their original files into one tar."""

load("@tar.bzl//tar:mtree.bzl", "mtree_spec")
load("@tar.bzl//tar:tar.bzl", "tar")

def _manifest_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".mtree")
    links = ctx.actions.declare_file(ctx.label.name + ".links.json")
    ctx.actions.write(links, json.encode(ctx.attr.symlinks))
    ctx.actions.run(
        executable = ctx.executable._tool,
        inputs = [ctx.file.spec, links],
        outputs = [output],
        arguments = [ctx.file.spec.path, output.path, ctx.label.package, links.path],
        mnemonic = "DebugSymbolsManifest",
    )
    return [DefaultInfo(files = depset([output]))]

_manifest = rule(
    implementation = _manifest_impl,
    attrs = {
        "spec": attr.label(mandatory = True, allow_single_file = True),
        "symlinks": attr.string_dict(),
        "_tool": attr.label(default = Label("//tar:debug_symbols_manifest"), executable = True, cfg = "exec"),
    },
)

def _debug_symbols_tar_impl(name, visibility, srcs, symlinks):
    spec = name + "_source_mtree"
    mtree_spec(name = spec, srcs = srcs, include_runfiles = False)
    manifest = name + "_install_mtree"
    _manifest(name = manifest, spec = ":" + spec, symlinks = symlinks)
    tar(
        name = name,
        srcs = srcs,
        mtree = ":" + manifest,
        compress = None,
        out = name + ".tar",
        visibility = visibility,
    )

debug_symbols_tar = macro(
    implementation = _debug_symbols_tar_impl,
    doc = "A tar of debug symbols laid out under /usr/lib/debug/.build-id.",
    attrs = {
        "symlinks": attr.string_dict(configurable = False, doc = "Additional installation symlinks, e.g. the dbgsym documentation link."),
        "srcs": attr.label_list(
            mandatory = True,
            allow_files = True,
            doc = "Targets producing .build-id/NN/REST.debug trees.",
        ),
    },
)
