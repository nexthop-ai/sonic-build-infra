"""Cargo release defaults scope the target graph and preserve caller settings.

Cargo profiles.html#release/#lto and rustc codegen-options.html#lto define opt3,
16 units and unspecified LTO (thin-local), rather than explicit -Clto=off.
"""
import json
import sys

query = json.load(open(sys.argv[1]))
configurations = {c["id"]: c for c in query["configuration"]}
targets = {t["id"]: t["label"] for t in query["targets"]}


def flags(arguments):
    values = {}
    for index, value in enumerate(arguments):
        setting = arguments[index + 1] if value == "-C" else value[2:] if value.startswith("-C") else value[len("--codegen="):] if value.startswith("--codegen=") else ""
        if "=" in setting:
            key, value = setting.split("=", 1)
            values[key] = value
    return values


actions = [a for a in query["actions"] if a["mnemonic"] == "Rustc" and not configurations[a["configurationId"]].get("isTool", False)]
roots = [a for a in actions if targets[a["targetId"]].endswith("//:probe.unprofiled")]
release = [a for a in roots if flags(a["arguments"]).get("opt-level") == "3" and flags(a["arguments"]).get("codegen-units") == "16"]
control = [a for a in roots if flags(a["arguments"]).get("opt-level") == "0" and flags(a["arguments"]).get("codegen-units") == "1"]
assert release and control, [(targets[a["targetId"]], flags(a["arguments"])) for a in roots]
assert all(flags(a["arguments"]).get("lto") == "fat" for a in control)
release_configurations = {a["configurationId"] for a in release}
profiled = [a for a in actions if a["configurationId"] in release_configurations]
for action in profiled:
    options = flags(action["arguments"])
    assert options.get("opt-level") == "3", (targets[action["targetId"]], options)
    assert options.get("codegen-units") == "16", (targets[action["targetId"]], options)
    assert "lto" not in options, (targets[action["targetId"]], options)
    assert options.get("embed-bitcode") == "no", (targets[action["targetId"]], options)
labels = [targets[a["targetId"]] for a in profiled]
assert any(label.endswith("//:probe_dependency") for label in labels), labels
assert any(label.endswith("//:regex") for label in labels), labels
print(json.dumps({"target_rust_actions_checked": len(profiled), "release_configurations": sorted(release_configurations), "caller_control_unchanged": True, "Cargo_lto_false": "unspecified / thin-local", "labels": labels}, indent=2))
