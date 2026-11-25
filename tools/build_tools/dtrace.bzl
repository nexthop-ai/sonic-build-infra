"""Generate a SystemTap DTrace semaphore object with the selected C toolchain."""
load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cpp_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _quote(words):
    return " ".join(["'" + word.replace("'", "'\"'\"'") + "'" for word in words])

def _impl(ctx):
    tc = find_cpp_toolchain(ctx)
    features = cc_common.configure_features(ctx = ctx, cc_toolchain = tc, requested_features = ctx.features, unsupported_features = ctx.disabled_features)
    contexts = [dep[CcInfo].compilation_context for dep in ctx.attr.deps]
    variables = cc_common.create_compile_variables(
        feature_configuration = features,
        cc_toolchain = tc,
        include_directories = depset(transitive = [c.includes for c in contexts]),
        quote_include_directories = depset(transitive = [c.quote_includes for c in contexts]),
        system_include_directories = depset(transitive = [c.system_includes for c in contexts] + [c.external_includes for c in contexts]),
        preprocessor_defines = depset(transitive = [c.defines for c in contexts]),
        user_compile_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.conlyopts,
    )
    compiler = [cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.c_compile)]
    compiler += cc_common.get_memory_inefficient_command_line(feature_configuration = features, action_name = ACTION_NAMES.c_compile, variables = variables)
    output = ctx.actions.declare_file(ctx.label.name + ".o")
    # The DTrace tool owns its temporary C source and emits a new PIC object.
    # The selected toolchain controls compiler flags; caller CFLAGS cannot
    # override mandatory target/compiler settings.
    ctx.actions.run(
        executable = ctx.executable.tool,
        arguments = ["-G", "-s", ctx.file.src.path, "-o", output.path],
        inputs = depset([ctx.file.src], transitive = [tc.all_files] + [c.headers for c in contexts]),
        tools = [ctx.attr.tool[DefaultInfo].files_to_run],
        outputs = [output],
        env = {"CC": _quote(compiler), "CFLAGS": "", "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": ctx.attr.source_date_epoch},
        mnemonic = "DTraceObject",
    )
    return [DefaultInfo(files = depset([output]))]

dtrace_object = rule(
    implementation = _impl,
    attrs = {
        "src": attr.label(mandatory = True, allow_single_file = True),
        "deps": attr.label_list(providers = [CcInfo]),
        "source_date_epoch": attr.string(mandatory = True),
        "tool": attr.label(default = Label("//tools/build_tools:dtrace"), executable = True, cfg = "exec"),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)
