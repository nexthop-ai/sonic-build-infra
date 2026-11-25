"""Bootstrap GNU patch from the shared APT lock for repository preparation."""
_HOSTS = {"amd64": ("amd64", "x86_64-linux-gnu", "ld-linux-x86-64.so.2"), "x86_64": ("amd64", "x86_64-linux-gnu", "ld-linux-x86-64.so.2"), "arm64": ("arm64", "aarch64-linux-gnu", "ld-linux-aarch64.so.1"), "aarch64": ("arm64", "aarch64-linux-gnu", "ld-linux-aarch64.so.1")}
_ENV = {"PATH": "", "LC_ALL": "C", "LD_PRELOAD": "", "LD_LIBRARY_PATH": "", "LD_AUDIT": "", "GLIBC_TUNABLES": ""}

def _impl(ctx):
    if ctx.os.name != "linux" or ctx.os.arch not in _HOSTS:
        fail("GNU source patch tools require an AMD64 or ARM64 Linux execution host")
    arch, triplet, loader = _HOSTS[ctx.os.arch]
    lock = json.decode(ctx.read(ctx.attr.lock))
    roots = {key + "=" + version: True for key, version in lock["dependency_sets"]["build_tools"]["sets"][arch].items() if lock["packages"][key + "=" + version]["name"] == "patch"}
    if len(roots) != 1:
        fail("The shared build_tools collection must declare one GNU patch root")
    selected = dict(roots)
    pending = roots.keys()
    for _ in range(len(lock["packages"])):
        if not pending:
            break
        children = {}
        for key in pending:
            for dep in lock["packages"][key]["depends_on"]:
                if dep not in selected:
                    children[dep] = True
        selected.update(children)
        pending = children.keys()
    for key in sorted(selected):
        package = lock["packages"][key]
        urls = [uri.rstrip("/") + "/" + package["filename"] for uri in lock["sources"][package["suite"]]["uris"]]
        ctx.download_and_extract(urls, "unpack", sha256 = package["sha256"], type = "deb")
        data = [path for path in ctx.path("unpack").readdir() if path.basename.startswith("data.tar")]
        if len(data) != 1:
            fail("Expected one Debian data member for " + key)
        ctx.extract(data[0], "root")
        ctx.delete("unpack")
    lib = "root/usr/lib/" + triplet
    ctx.symlink(lib + "/" + loader, "loader")
    ctx.symlink(lib, "lib")
    ctx.symlink("root/usr/bin/patch", "patch")
    argv = [ctx.path("loader"), "--inhibit-cache", "--library-path", ctx.path("lib")]
    check = ctx.execute(argv + ["--list", ctx.path("patch")], environment = _ENV)
    if check.return_code:
        fail("Cannot resolve GNU patch execution libraries: " + check.stderr)
    for line in check.stdout.splitlines():
        line = line.strip()
        if line.startswith("linux-vdso.so.1 ("):
            continue
        if not line.split(" => ", 1)[-1].startswith(str(ctx.path(".")) + "/"):
            fail("GNU patch resolved outside its locked execution root: " + line)
    version = ctx.execute(argv + [ctx.path("patch"), "--version"], environment = _ENV)
    if version.return_code or not version.stdout.startswith("GNU patch "):
        fail("The declared APT tool is not GNU patch")
    ctx.file("manifest.json", json.encode({"architecture": arch, "roots": roots.keys(), "packages": {key: lock["packages"][key]["sha256"] for key in sorted(selected)}, "version": version.stdout}))
    ctx.file("ROOT", "", executable = False)
    ctx.file("BUILD.bazel", 'exports_files(["ROOT", "manifest.json"], visibility=["//visibility:public"])\n')

patch_tools_repository = repository_rule(implementation = _impl, attrs = {"lock": attr.label(allow_single_file = True, mandatory = True)})

def gnu_patch(ctx, patch, strip, fuzz):
    """Execute only an explicitly selected GNU recipe; never retry native errors."""
    if ctx.attr.gnu_tools == None:
        fail("GNU patch requires a declared locked execution tool root")
    root = ctx.path(ctx.attr.gnu_tools).dirname
    # execute() does not track files read by the external program.
    ctx.read(patch)
    ctx.read(root.get_child("manifest.json"))
    result = ctx.execute([root.get_child("loader"), "--inhibit-cache", "--library-path", root.get_child("lib"), root.get_child("patch"), "--batch", "--forward", "--fuzz=" + str(fuzz), "-p" + str(strip), "--input=" + str(patch)], environment = _ENV)
    if result.return_code:
        fail("Declared GNU patch step failed:\n" + result.stdout + result.stderr)
