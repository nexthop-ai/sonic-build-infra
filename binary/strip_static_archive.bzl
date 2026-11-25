"""Strip static-archive debug information with the target binutils toolchain."""

load("//toolchains/binutils:binutils_toolchain.bzl", "BINUTILS_TOOLCHAIN_TYPE")

def _strip_static_archive_impl(ctx):
    objcopy = ctx.toolchains[BINUTILS_TOOLCHAIN_TYPE].binutils.objcopy
    args = ctx.actions.args()
    args.add("--strip-debug")
    args.add(ctx.file.src)
    args.add(ctx.outputs.out)
    ctx.actions.run(
        executable = objcopy,
        tools = [objcopy],
        inputs = [ctx.file.src],
        outputs = [ctx.outputs.out],
        arguments = [args],
        env = {"LC_ALL": "C"},
        mnemonic = "StripStaticArchive",
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]))]

strip_static_archive = rule(
    implementation = _strip_static_archive_impl,
    doc = "Remove archive-member DWARF while preserving target objects and link symbols.",
    attrs = {
        "src": attr.label(allow_single_file = [".a"], mandatory = True),
        "out": attr.output(mandatory = True),
    },
    toolchains = [BINUTILS_TOOLCHAIN_TYPE],
)
