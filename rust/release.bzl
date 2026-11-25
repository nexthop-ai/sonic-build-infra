"""Apply Cargo's default release optimization to a native Rust target graph.

Cargo lto=false retains rustc's thin-local LTO; an explicit -Clto=off would
change that contract. rules_rust's public unspecified setting preserves it.
"""

_LTO = str(Label("@rules_rust//rust/settings:lto"))
_CODEGEN_UNITS = str(Label("@rules_rust//rust/settings:codegen_units"))

def _release_impl(_settings, _attr):
    return {
        "//command_line_option:compilation_mode": "opt",
        _LTO: "unspecified",
        _CODEGEN_UNITS: 16,
    }

_release = transition(implementation = _release_impl, inputs = [], outputs = [
    "//command_line_option:compilation_mode", _LTO, _CODEGEN_UNITS,
])

def _impl(ctx):
    info = ctx.attr.src[0][DefaultInfo]
    return [DefaultInfo(files = info.files, default_runfiles = info.default_runfiles, data_runfiles = info.data_runfiles)]

cargo_release_binary = rule(
    implementation = _impl,
    doc = "Forward a binary built with Cargo's default release optimization, Cargo lto=false (thin-local) and sixteen codegen units; test targets keep their own profile.",
    attrs = {
        "src": attr.label(mandatory = True, allow_single_file = True, cfg = _release),
        "_allowlist_function_transition": attr.label(default = "@bazel_tools//tools/allowlists/function_transition_allowlist"),
    },
)
