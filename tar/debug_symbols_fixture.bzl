"""Generated symbol tree used to check the public archive contract."""

def _symbols_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name + "/symbols")
    ctx.actions.run_shell(
        outputs = [output],
        arguments = [output.path],
        command = "mkdir -p \"$1/.build-id/cd\" && printf 'tree symbols\\n' > \"$1/.build-id/cd/tree with space.debug\"",
    )
    return [DefaultInfo(files = depset([output]))]

symbol_tree = rule(implementation = _symbols_impl)
