"""Verify real ELF inputs produce Debian controls without a DEB producer.

The shared library calls libc; the executable calls that library. Their resolved
dependencies must follow the target binaries, including source-package versions
read from the changelog, and metadata must identify the exact supplied TARs.
"""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import struct
import sys
import tarfile
import tempfile
import unittest


metadata, library, program, generator, tools, tar, control, changelog = map(Path, sys.argv[1:9])
architecture = sys.argv[9]
patchelf = Path(sys.argv[10])
libc_data, libc_control = map(Path, sys.argv[11:13])
finalized_control, finalized_service = map(Path, sys.argv[13:15])


class RuntimePackagesRuleTest(unittest.TestCase):
    def generate(self, root, source_control, payload, package="metadata-library", additional=(), source_template=True):
        return subprocess.run([
            str(generator.resolve()), "--control", str(source_control),
            *(["--changelog", str(changelog.resolve())] if source_template else []),
            "--module", "sonic-build-infra",
            "--architecture", architecture, "--tools", str(tools.resolve()),
            "--tar", str(tar.resolve()), "--output", str(root / "metadata.json"),
            "--tree", str(root / "packages"),
            "--package", package + "=//oci/tests:metadata_library_tar=" + str(payload.resolve()),
            # Deliberately no target APT package or source dependency.
            *additional,
        ], text=True, capture_output=True)

    def control(self, root, package, relationships, architecture="any"):
        source_control = root / "control"
        source_control.write_text(
            "Source: metadata-example\nMaintainer: Example <example@example.org>\n"
            "Section: libs\nPriority: optional\nBuild-Depends: debhelper-compat (= 13)\n\n"
            "Package: " + package + "\nArchitecture: " + architecture + "\n"
            + relationships + "\nDescription: Generated metadata helper fixture.\n")
        return source_control

    def test_existing_debian_fields_and_actual_library_dependencies(self):
        document = json.loads(metadata.read_text())
        packages = document["packages"]
        self.assertEqual(set(packages), {"metadata-library", "metadata-cli"})
        for name, item in packages.items():
            with self.subTest(package=name):
                fields = item["control_fields"]
                self.assertEqual(fields["Package"], name)
                self.assertEqual(fields["Version"], "2.0")
                self.assertEqual(fields["Maintainer"], "Example <example@example.org>")
                self.assertNotIn("${", json.dumps(fields))
                self.assertEqual(fields["Architecture"], document["architecture"])
        self.assertIn("libc6 (>=", packages["metadata-library"]["control_fields"]["Depends"])
        self.assertIn("metadata-library (>= 2.0)", packages["metadata-cli"]["control_fields"]["Depends"])
        self.assertEqual(packages["metadata-library"]["required_paths"], ["usr/lib/libmetadata-fixture.so.1"])
        self.assertEqual(packages["metadata-cli"]["required_paths"], ["usr/bin/metadata-cli"])
        self.assertEqual(packages["metadata-library"]["runtime_target"], "//oci/tests:metadata_library_tar")
        self.assertEqual(packages["metadata-cli"]["runtime_target"], "//oci/tests:metadata_cli_tar")
        self.assertEqual(packages["metadata-library"]["payload_sha256"], hashlib.sha256(library.read_bytes()).hexdigest())
        self.assertEqual(packages["metadata-cli"]["payload_sha256"], hashlib.sha256(program.read_bytes()).hexdigest())

    def test_omitted_reviewed_file_prevents_metadata_publication(self):
        """Run the real generator against a valid TAR missing a declared file."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.generate(root, control.resolve(), library,
                additional=["--required-path", "metadata-library=usr/lib/missing-required-soname.so.0"])
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("runtime package lacks required paths", result.stderr)
            self.assertFalse((root / "metadata.json").exists())

    def test_per_package_helpers_reject_unknown_owners_and_commands(self):
        cases = [({"unselected-package": [["dh_installdeb"]]}, "undeclared package"),
                 ({"metadata-library": [["unknown-helper"], ["dh_installdeb"]]}, "invalid declared"),
                 ({"metadata-library": [["dh_installinit"]]}, "finish with dh_installdeb")]
        for mapping, error in cases:
            with self.subTest(mapping=mapping), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = self.generate(root, control.resolve(), library,
                    additional=["--control-file-helpers-by-package", json.dumps(mapping)])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertFalse((root / "metadata.json").exists())

    def test_missing_target_library_cannot_fall_back_to_execution_runtime(self):
        """Declared dpkg tools include libc, but that libc cannot satisfy a target dependency."""
        self.assertTrue(list((tools / "usr/lib").glob("*/libc.so.6")))
        for remove_depends in (False, True):
            with self.subTest(control_has_depends=not remove_depends), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source_control = root / "control"
                source_control.write_text("\n".join(
                    line for line in control.read_text().splitlines()
                    if not remove_depends or not line.startswith("Depends:")) + "\n")
                result = self.generate(root, source_control, library)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertRegex(result.stderr, r"(?:cannot|couldn't) find library libc\.so\.6")
                self.assertFalse((root / "metadata.json").exists())

    def test_absolute_rpath_cannot_escape_declared_target_package_roots(self):
        """An ELF's embedded search path cannot opt into the execution tool's libc."""
        execution_libc = next((tools.resolve() / "usr/lib").glob("*/libc.so.6"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "library.so"
            with tarfile.open(library) as archive:
                member = next(item for item in archive if item.isfile() and item.name.endswith(".so.1"))
                binary.write_bytes(archive.extractfile(member).read())
            subprocess.run([str(patchelf.absolute()), "--set-rpath", str(execution_libc.parent), str(binary)], check=True)
            payload = root / "rpath.tar"
            data = binary.read_bytes()
            with tarfile.open(payload, "w") as archive:
                member = tarfile.TarInfo("usr/lib/libmetadata-fixture.so.1")
                member.size, member.mode = len(data), 0o755
                archive.addfile(member, io.BytesIO(data))
            result = self.generate(root, control.resolve(), payload)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stderr, r"(?:cannot|couldn't) find library libc\.so\.6")

    def test_standard_helpers_resolve_script_service_and_debconf_dependencies(self):
        """Existing Debian helper inputs supply their dependencies without handwritten versions."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "metadata-scripts",
                "Depends: ${python3:Depends}, ${perl:Depends}, ${misc:Depends}, ${misc:Pre-Depends}", architecture="all")
            payload = root / "scripts.tar"
            with tarfile.open(payload, "w") as archive:
                for name, data in {
                    "usr/bin/metadata-python": b"#!/usr/bin/python3\nprint('fixture')\n",
                    "usr/bin/metadata-perl": b"#!/usr/bin/perl\nprint qq(fixture\\n);\n",
                    "etc/init.d/custom-service": b"#!/bin/sh\nexit 0\n",
                }.items():
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = len(data), 0o755
                    archive.addfile(member, io.BytesIO(data))
            templates = root / "templates"
            templates.write_text("Template: metadata-scripts/enabled\nType: boolean\nDefault: false\nDescription: Example question\n")
            result = self.generate(root, source_control, payload, package="metadata-scripts",
                additional=["--debian-file", "metadata-scripts.templates=" + str(templates)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = json.loads((root / "metadata.json").read_text())["packages"]["metadata-scripts"]["control_fields"]
            for name in ("python3", "perl", "debconf", "init-system-helpers"):
                self.assertIn(name, fields["Depends"])
            self.assertNotIn("${", fields["Depends"])

    def test_finalized_control_keeps_service_metadata_without_a_source_recipe(self):
        """The actual OTEL release control/unit need no fabricated compat or helper dependencies."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "otel-service.tar"
            with tarfile.open(payload, "w") as archive:
                data = finalized_service.read_bytes()
                unit = tarfile.TarInfo("lib/systemd/system/otelcol-contrib.service")
                unit.size, unit.mode = len(data), 0o644
                archive.addfile(unit, io.BytesIO(data))
            result = self.generate(root, finalized_control.resolve(), payload,
                package="otelcol-contrib", source_template=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads((root / "metadata.json").read_text())
            fields = document["packages"]["otelcol-contrib"]["control_fields"]
            self.assertEqual(fields["Package"], "otelcol-contrib")
            self.assertEqual(fields["Version"], "0.144.0")
            self.assertEqual(fields["Maintainer"],
                "The OpenTelemetry Collector maintainers <cncf-opentelemetry-maintainers@lists.cncf.io>")
            self.assertEqual(fields["Description"], "OpenTelemetry Collector - otelcol-contrib")
            self.assertNotIn("Depends", fields)
            self.assertNotIn("Pre-Depends", fields)
            self.assertEqual(document["architecture"], architecture)
            staged = root / "packages/packages/otelcol-contrib"
            self.assertEqual((staged / unit.name).read_bytes(), finalized_service.read_bytes())
            self.assertEqual({item.name for item in (staged / "DEBIAN").iterdir()}, {"control"})
            self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(),
                document["packages"]["otelcol-contrib"]["payload_sha256"])

    def test_source_template_still_runs_service_helpers_and_requires_the_recipe(self):
        """The same unit uses real helpers for source templates, including their compat checks."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "metadata-scripts", "Depends: ${misc:Depends}")
            payload = root / "service.tar"
            with tarfile.open(payload, "w") as archive:
                data = finalized_service.read_bytes()
                unit = tarfile.TarInfo("lib/systemd/system/metadata-scripts.service")
                unit.size, unit.mode = len(data), 0o644
                archive.addfile(unit, io.BytesIO(data))
            result = self.generate(root, source_control, payload, package="metadata-scripts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = json.loads((root / "metadata.json").read_text())["packages"]["metadata-scripts"]["control_fields"]
            self.assertEqual(fields["Version"], "2.0")
            self.assertNotIn("${", json.dumps(fields))
            source_control.write_text(source_control.read_text().replace("Build-Depends: debhelper-compat (= 13)\n", ""))
            missing_recipe = root / "missing-recipe"
            missing_recipe.mkdir()
            result = self.generate(missing_recipe, source_control, payload, package="metadata-scripts")
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Compatibility levels before 7 are no longer supported", result.stderr)
            self.assertFalse((missing_recipe / "metadata.json").exists())

    def test_finalized_control_still_resolves_actual_elf_dependencies(self):
        """Finalized dependency text cannot substitute for the declared target library."""
        fields = dict(json.loads(metadata.read_text())["packages"]["metadata-library"]["control_fields"])
        fields["Architecture"] = architecture
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary_control = root / "control"
            binary_control.write_text("".join(name + ": " + value + "\n" for name, value in fields.items()))
            result = self.generate(root, binary_control, library, source_template=False)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stderr, r"(?:cannot|couldn't) find library libc\.so\.6")
            self.assertFalse((root / "metadata.json").exists())
            supplied = root / "with-target-library"
            supplied.mkdir()
            result = self.generate(supplied, binary_control, library, source_template=False,
                additional=["--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve())])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            generated = json.loads((supplied / "metadata.json").read_text())["packages"]["metadata-library"]
            self.assertEqual(generated["control_fields"]["Depends"], fields["Depends"])
            self.assertEqual(generated["payload_sha256"], hashlib.sha256(library.read_bytes()).hexdigest())

    def test_time64_provides_comes_from_debhelper(self):
        """Let Debian's architecture-aware helper determine the compatibility Provides field."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "libmetadatat64",
                "Depends: ${shlibs:Depends}, ${misc:Pre-Depends}\nProvides: ${t64:Provides}")
            result = self.generate(root, source_control, library, package="libmetadatat64",
                additional=["--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve())])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = json.loads((root / "metadata.json").read_text())["packages"]["libmetadatat64"]["control_fields"]
            self.assertEqual(fields["Provides"], "libmetadata (= 2.0)")

    def test_source_shlibs_substitutions_preserve_dependency_fields(self):
        """Debian control chooses shlibs fields; real ELF dependencies follow them."""
        cases = [
            ("Depends: ${shlibs:Depends}", "Depends"),
            ("Pre-Depends: ${shlibs:Pre-Depends}\nDepends: base-files", "Pre-Depends"),
            ("Recommends: ${shlibs:Recommends}", "Recommends"),
            ("Recommends: ${shlibs:Depends}", "Recommends"),
            ("Pre-Depends: ${shlibs:Pre-Depends}\nDepends: base-files, ${shlibs:Depends}", "Pre-Depends"),
        ]
        for relationships, dependency_field in cases:
            with self.subTest(relationships=relationships), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source_control = self.control(root, "metadata-library", relationships)
                result = self.generate(root, source_control, library,
                    additional=["--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve())])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                item = json.loads((root / "metadata.json").read_text())["packages"]["metadata-library"]
                fields = item["control_fields"]
                self.assertIn("libc6 (>=", fields[dependency_field])
                if "base-files" in relationships:
                    self.assertEqual(fields["Depends"], "base-files")
                elif dependency_field != "Depends":
                    self.assertNotIn("Depends", fields)
                self.assertNotIn("${", json.dumps(fields))
                self.assertEqual(item["payload_sha256"], hashlib.sha256(library.read_bytes()).hexdigest())

    def test_declared_symbols_check_level_preserves_real_dpkg_policy(self):
        for level in (0, 4):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                template = root / "symbols"
                template.write_text("libmetadata-fixture.so.1 metadata-library #MINVER#\n removed_symbol@Base 1.0\n")
                result = self.generate(root, control.resolve(), library, additional=[
                    "--symbols", "metadata-library=" + str(template),
                    "--symbols-check-level", str(level),
                    "--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve()),
                ])
                if level == 0:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("dpkg-gensymbols", result.stderr)
                    self.assertFalse((root / "metadata.json").exists())

    def test_original_plugin_symbols_exclusion_keeps_strict_abi_and_needed_checks(self):
        """Krb's -X applies to public symbols, not its installed plugin's NEEDED."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with tarfile.open(library) as archive:
                member = next(item for item in archive if item.isfile() and item.name.endswith(".so.1"))
                data = archive.extractfile(member).read()
            plugin = root / "plugin.so"
            plugin.write_bytes(data)
            subprocess.run([str(patchelf.absolute()), "--set-soname", "metadata-plugin.so.0", str(plugin)], check=True)
            original = {"usr/lib/libmetadata-fixture.so.1": data,
                        "usr/lib/krb5/plugins/preauth/plugin.so": plugin.read_bytes()}
            payload = root / "plugin.tar"
            with tarfile.open(payload, "w") as archive:
                for path, contents in original.items():
                    item = tarfile.TarInfo(path)
                    item.size, item.mode = len(contents), 0o755
                    archive.addfile(item, io.BytesIO(contents))
            template = root / "symbols"
            # The independent fixture C source defines exactly metadata_value.
            template.write_text("libmetadata-fixture.so.1 metadata-library #MINVER#\n metadata_value@Base 2.0\n")
            common = ["--symbols", "metadata-library=" + str(template), "--symbols-check-level", "4"]
            excludes = ["--symbols-excludes", json.dumps({"metadata-library": ["krb5/plugins"]})]
            apt = ["--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve())]
            for name, extra, error in [("unfiltered", common + apt, "new libraries"),
                                       ("missing-needed", common + excludes, r"(?:cannot|couldn't) find library libc\.so\.6"),
                                       ("preserved", common + excludes + apt, None)]:
                stage = root / name
                stage.mkdir()
                result = self.generate(stage, control.resolve(), payload, additional=extra)
                if error:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertRegex(result.stderr, error)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    info = json.loads((stage / "metadata.json").read_text())["packages"]["metadata-library"]
                    self.assertIn("libc6 (>=", info["control_fields"]["Depends"])
                    inventory = info["elf_inventory"]["usr/lib/krb5/plugins/preauth/plugin.so"]
                    self.assertEqual(inventory["role"], "runtime")
                    self.assertFalse(inventory["symbols"])
                    self.assertTrue(inventory["shlibdeps"])
                    symbols = (stage / "packages/packages/metadata-library/DEBIAN/symbols").read_text()
                    self.assertNotIn("metadata-plugin", symbols)
                    for path, contents in original.items():
                        self.assertEqual((stage / "packages/packages/metadata-library" / path).read_bytes(), contents)

            removed = root / "removed-library.tar"
            with tarfile.open(removed, "w") as archive:
                data = original["usr/lib/krb5/plugins/preauth/plugin.so"]
                item = tarfile.TarInfo("usr/lib/krb5/plugins/preauth/plugin.so")
                item.size, item.mode = len(data), 0o755
                archive.addfile(item, io.BytesIO(data))
            missing = root / "missing-public-library"
            missing.mkdir()
            result = self.generate(missing, control.resolve(), removed, additional=common + excludes + apt)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("libraries disappeared", result.stderr)

    def test_explicit_source_role_preserves_cross_target_objects_and_fixtures(self):
        """Original Go source selection is data even for an arch-specific source recipe."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "metadata-source", "Depends: base-files", architecture="any")
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<HH", header, 16, 1, 183 if architecture == "amd64" else 62)
            obj = bytes(header)
            struct.pack_into("<H", header, 16, 3)
            original = {"usr/share/example/src/race.syso": obj,
                        "usr/share/example/src/testdata/foreign.so": bytes(header)}
            with tarfile.open(library) as archive:
                member = next(item for item in archive if item.isfile() and item.name.endswith(".so.1"))
                original["usr/share/example/src/testdata/libmetadata-fixture.so.1"] = archive.extractfile(member).read()
            payload = root / "source.tar"
            with tarfile.open(payload, "w") as archive:
                for path, data in original.items():
                    item = tarfile.TarInfo(path)
                    item.size, item.mode = len(data), 0o644
                    archive.addfile(item, io.BytesIO(data))
            extra = ["--package-roles", json.dumps({"metadata-source": "source"}),
                     "--symbols-excludes", json.dumps({"metadata-source": ["testdata", "test"]}),
                     "--shlibdeps-excludes", json.dumps({"metadata-source": ["testdata", "test"]})]
            stage = root / "source"
            stage.mkdir()
            result = self.generate(stage, source_control, payload, package="metadata-source", additional=extra)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            info = json.loads((stage / "metadata.json").read_text())["packages"]["metadata-source"]
            self.assertEqual(info["control_fields"]["Architecture"], architecture)
            self.assertEqual(info["control_fields"]["Depends"], "base-files")
            self.assertEqual(info["payload_sha256"], hashlib.sha256(payload.read_bytes()).hexdigest())
            for path, data in original.items():
                self.assertEqual((stage / "packages/packages/metadata-source" / path).read_bytes(), data)
                self.assertEqual(info["elf_inventory"][path]["sha256"], hashlib.sha256(data).hexdigest())
            strict = root / "default-runtime"
            strict.mkdir()
            result = self.generate(strict, source_control, payload, package="metadata-source")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target architecture", result.stderr)
            # A source fixture with the correct SONAME and architecture still
            # cannot satisfy a real target executable's dependency.
            consumer = root / "consumer"
            consumer.mkdir()
            result = self.generate(consumer, control.resolve(), program, package="metadata-cli", additional=[
                "--dependency", str(stage / "packages"),
                "--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve()),
            ])
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stderr, r"(?:cannot|couldn't) find library libmetadata-fixture\.so\.1")

    def test_elf_selection_rejects_unmapped_packages_and_invalid_roles(self):
        cases = [("--symbols-excludes", {"other": ["plugins"]}),
                 ("--shlibdeps-excludes", {"metadata-library": [""]}),
                 ("--package-roles", {"other": "source"}),
                 ("--package-roles", {"metadata-library": "ignored"})]
        for flag, mapping in cases:
            with self.subTest(flag=flag, mapping=mapping), tempfile.TemporaryDirectory() as temporary:
                result = self.generate(Path(temporary), control.resolve(), library,
                                       additional=[flag, json.dumps(mapping)])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid per-package ELF", result.stderr)

    def test_resource_heavy_payload_does_not_expand_target_search_environment(self):
        """Data directories need no per-directory ELF authorization to dpkg."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "metadata-resources", "Depends: base-files", architecture="all")
            payload = root / "data.tar"
            with tarfile.open(payload, "w") as archive:
                # Resource catalogs such as sonic-device-data have thousands of
                # independent directories. This is a regression input, not a limit.
                for index in range(2000):
                    member = tarfile.TarInfo("usr/share/catalog/" + str(index) + "-" + "resource" * 16 + "/empty")
                    archive.addfile(member)
            result = self.generate(root, source_control, payload, package="metadata-resources")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            package = json.loads((root / "metadata.json").read_text())["packages"]["metadata-resources"]
            self.assertEqual(package["control_fields"]["Depends"], "base-files")
            self.assertEqual(package["control_fields"]["Architecture"], "all")
            self.assertEqual(package["payload_sha256"], hashlib.sha256(payload.read_bytes()).hexdigest())

    def test_non_elf_payload_has_no_shlibs_pre_dependencies(self):
        """A source's shlibs variable resolves empty when its TAR has no ELF."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "metadata-scripts",
                "Pre-Depends: ${shlibs:Pre-Depends}\nDepends: base-files", architecture="all")
            payload = root / "data.tar"
            with tarfile.open(payload, "w") as archive:
                data = b"plain runtime resource\n"
                member = tarfile.TarInfo("usr/share/metadata/resource")
                member.size, member.mode = len(data), 0o644
                archive.addfile(member, io.BytesIO(data))
            result = self.generate(root, source_control, payload, package="metadata-scripts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = json.loads((root / "metadata.json").read_text())["packages"]["metadata-scripts"]["control_fields"]
            self.assertEqual(fields["Depends"], "base-files")
            self.assertNotIn("Pre-Depends", fields)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
