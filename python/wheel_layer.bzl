"""Install declared wheel artifacts into a reproducible OCI filesystem layer."""

load(":source_package.bzl", "PythonRuntimeInfo")

def _wheel_layer_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".tar")
    metadata = [ctx.actions.declare_file(ctx.label.name + ".wheel" + str(index) + ".runtime.json") for index in range(len(ctx.files.wheels))]
    producers = {file.path: str(target.label) for target in ctx.attr.wheels for file in target[DefaultInfo].files.to_list()}
    args = ctx.actions.args()
    args.add("--output", output)
    args.add("--site-packages", ctx.attr.site_packages)
    args.add("--scripts", ctx.attr.scripts)
    args.add("--headers", ctx.attr.headers)
    args.add("--data", ctx.attr.data_prefix)
    args.add("--interpreter", ctx.attr.interpreter)
    args.add_all(metadata, before_each = "--metadata-output")
    args.add_all(ctx.files.wheels)
    ctx.actions.run(
        executable = ctx.attr._tool[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = ctx.files.wheels,
        outputs = [output] + metadata,
        mnemonic = "WheelLayer",
        progress_message = "Install Python wheels for %{label}",
    )
    info = PythonRuntimeInfo(
        packages = depset([
            struct(
                producer = producers[wheel.path],
                tar = output,
                metadata = metadata[index],
                wheel = wheel,
            )
            for index, wheel in enumerate(ctx.files.wheels)
        ], transitive = [target[PythonRuntimeInfo].packages for target in ctx.attr.runtime_deps]),
        tars = depset([output], transitive = [target[PythonRuntimeInfo].tars for target in ctx.attr.runtime_deps]),
        metadata = depset(metadata, transitive = [target[PythonRuntimeInfo].metadata for target in ctx.attr.runtime_deps]),
    )
    return [
        DefaultInfo(files = depset([output])),
        info,
        OutputGroupInfo(metadata = info.metadata, runtime_tars = info.tars),
    ]

wheel_layer = rule(
    implementation = _wheel_layer_impl,
    doc = """Install wheels, including metadata, data files and entry points.

    Inputs must contain the full runtime dependency closure. This rule does not
    resolve or download dependencies, execute package code, or compile bytecode.
    The caller supplies paths for the target image, independently of the Python
    interpreter running the installation tool on the execution platform.

    PythonRuntimeInfo and the metadata output group describe the exact wheels
    installed by this action, for python_runtime_plan/checked_python_runtime.
    Keep owner wheels and locked external wheels in separate layers when using
    those checks. runtime_deps propagates source metadata/TARs without installing
    those dependencies in this layer. DefaultInfo remains this layer's TAR only.
    """,
    attrs = {
        "wheels": attr.label_list(mandatory = True, allow_files = [".whl"]),
        "site_packages": attr.string(mandatory = True),
        "scripts": attr.string(default = "/usr/local/bin"),
        "headers": attr.string(default = "/usr/local/include"),
        "data_prefix": attr.string(default = "/usr/local"),
        "interpreter": attr.string(default = "/usr/bin/python3"),
        "runtime_deps": attr.label_list(providers = [PythonRuntimeInfo]),
        "_tool": attr.label(
            default = Label("//python:wheel_layer_builder"),
            executable = True,
            cfg = "exec",
        ),
    },
)
