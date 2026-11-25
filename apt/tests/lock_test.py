"""APT lock contract: one immutable package graph, owned requests and safe update.

Expectations follow the user-approved source/lock design and explicit fixture
identities, independently of the resolver and implementation constants.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import lock


def source_document(uri="https://snapshot.example/debian/20260727"):
    return {"version": 1, "sources": [{"uris": [uri], "suites": ["stable"],
                                      "components": ["main"], "architectures": ["amd64", "arm64"]}]}


def request_document(packages=None):
    return {"version": 1, "dependency_sets": {
        "runtime": {"packages": packages or ["demo (>= 1)"], "suites": ["stable"], "include_transitive": True}}}


def package(name="demo", version="1", architecture="amd64", dependencies=None):
    return {"name": name, "version": version, "architecture": architecture, "suite": "stable",
            "filename": f"pool/{name}_{version}_{architecture}.deb", "sha256": "a" * 64,
            "depends_on": dependencies or [], "files": [f"usr/lib/{name}.so"]}


def graph():
    return {"version": 2,
            "sources": {"stable": {"uris": ["https://snapshot.example/debian/20260727"],
                                     "components": ["main"], "architectures": ["amd64", "arm64"], "types": []}},
            "packages": {"/stable/demo:amd64=1": package(dependencies=["/stable/helper:amd64=2"]),
                         "/stable/helper:amd64=2": package("helper", "2")},
            "dependency_sets": {"runtime": {"sets": {"amd64": {"/stable/demo:amd64": "1"}}}},
            "facts": {"stable/main/amd64/Packages/index": "sha256-example"}}


class LockContractTest(unittest.TestCase):
    def finalized(self):
        return lock.finalize_result(graph(), graph(), source_document(), request_document(), "fixture-resolver")

    def test_merge_deduplicates_identity_and_preserves_extraction_metadata(self):
        first, second = graph(), graph()
        second["packages"]["/stable/demo:amd64=1"]["payload_sha256"] = "b" * 64
        second["dependency_sets"] = {"tools": second["dependency_sets"]["runtime"]}
        merged = lock.merge_locks([first, second])
        self.assertEqual(len(merged["packages"]), 2)
        self.assertEqual(set(merged["dependency_sets"]), {"runtime", "tools"})
        self.assertEqual(merged["packages"]["/stable/demo:amd64=1"]["payload_sha256"], "b" * 64)

    def test_merge_rejects_conflicting_identity_hash(self):
        other = graph()
        other["packages"]["/stable/demo:amd64=1"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "conflicting package sha256"):
            lock.merge_locks([graph(), other])

    def test_missing_transitive_package_is_rejected(self):
        broken = graph()
        del broken["packages"]["/stable/helper:amd64=2"]
        with self.assertRaisesRegex(ValueError, "missing from lock"):
            lock.validate_lock(broken)

    def test_changed_requests_stale_even_if_versions_still_satisfy(self):
        locked = self.finalized()
        lock.check_lock(locked, source_document(), request_document(), "fixture-resolver")
        with self.assertRaisesRegex(ValueError, "stale"):
            lock.check_lock(locked, source_document(), request_document(["demo (>= 0.9)"]), "fixture-resolver")

    def test_changed_source_stales_lock(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            lock.check_lock(self.finalized(), source_document("https://snapshot.example/debian/20260801"), request_document(), "fixture-resolver")

    def test_new_version_cannot_silently_replace_existing(self):
        candidate = graph()
        del candidate["packages"]["/stable/helper:amd64=2"]
        candidate["packages"]["/stable/helper:amd64=3"] = package("helper", "3")
        candidate["packages"]["/stable/demo:amd64=1"]["depends_on"] = ["/stable/helper:amd64=3"]
        with self.assertRaisesRegex(ValueError, "version changed"):
            lock.finalize_result(candidate, graph(), source_document(), request_document(), "fixture-resolver")

    def test_existing_payload_metadata_survives_new_export(self):
        previous = graph()
        previous["packages"]["/stable/demo:amd64=1"]["control_sha256"] = "b" * 64
        result = lock.finalize_result(graph(), previous, source_document(), request_document(), "fixture-resolver")
        self.assertEqual(result["packages"]["/stable/demo:amd64=1"]["control_sha256"], "b" * 64)

    def test_source_change_requires_explicit_authorization(self):
        candidate = graph()
        candidate["sources"]["stable"]["uris"] = ["https://snapshot.example/debian/20260801"]
        sources = source_document("https://snapshot.example/debian/20260801")
        with self.assertRaisesRegex(ValueError, "allow-source-change"):
            lock.finalize_result(candidate, graph(), sources, request_document(), "fixture-resolver")
        result = lock.finalize_result(candidate, graph(), sources, request_document(), "fixture-resolver", allow_source_change=True)
        self.assertEqual(result["packages"]["/stable/demo:amd64=1"]["version"], "1")

    def test_duplicate_suite_conflicts_fail(self):
        document = source_document()
        document["sources"].append(dict(document["sources"][0], uris=["https://different.example/"]))
        with self.assertRaisesRegex(ValueError, "conflicting source"):
            lock.source_records(document)

    def test_unrelated_existing_package_is_preserved(self):
        previous = graph()
        previous["packages"]["/stable/owned_elsewhere:amd64=9"] = package("owned_elsewhere", "9")
        result = lock.finalize_result(graph(), previous, source_document(), request_document(), "fixture-resolver")
        self.assertEqual(result["packages"]["/stable/owned_elsewhere:amd64=9"], previous["packages"]["/stable/owned_elsewhere:amd64=9"])

    def test_new_dependency_order_is_stable_from_first_update(self):
        previous = graph()
        alpha, zeta = "/stable/alpha:amd64=1", "/stable/zeta:amd64=1"
        owner, added = "/stable/owned_elsewhere:amd64=9", "/stable/added:amd64=1"
        previous["packages"].update({alpha: package("alpha"), zeta: package("zeta"),
                                     owner: package("owned_elsewhere", "9", dependencies=[zeta, alpha])})
        candidate = graph()
        candidate["packages"].update({alpha: package("alpha"), zeta: package("zeta"),
                                      added: package("added", dependencies=[zeta, "/stable/helper:amd64=2", alpha])})
        candidate["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/added:amd64"] = "1"
        requests = request_document(["demo (>= 1)", "added (= 1)"])
        first = lock.finalize_result(candidate, previous, source_document(), requests, "fixture-resolver")
        permuted = deepcopy(candidate)
        permuted["packages"][added]["depends_on"].reverse()
        other_first = lock.finalize_result(permuted, previous, source_document(), requests, "fixture-resolver")
        self.assertEqual(first, other_first)
        # An unselected reviewed record retains all fields, including its
        # existing dependency array order, rather than being normalized again.
        self.assertEqual(first["packages"][owner], previous["packages"][owner])
        repeated = lock.finalize_result(permuted, first, source_document(), requests, "fixture-resolver")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt.lock.json"
            lock.atomic_write(path, first)
            initial_bytes = path.read_bytes()
            lock.atomic_write(path, repeated)
            self.assertEqual(path.read_bytes(), initial_bytes)
        self.assertEqual(repeated["packages"][owner], previous["packages"][owner])

    def test_new_lock_is_world_readable_with_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt.lock.json"
            prior_umask = os.umask(0o077)
            try:
                lock.atomic_write(path, self.finalized())
            finally:
                os.umask(prior_umask)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            lock.check_lock(json.loads(path.read_text()), source_document(), request_document(), "fixture-resolver")

    def test_update_preserves_existing_lock_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            for permission in (0o640, 0o444, 0o660):
                with self.subTest(permission=oct(permission)):
                    path = Path(directory) / oct(permission)
                    path.write_text("old input")
                    path.chmod(permission)
                    lock.atomic_write(path, self.finalized())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), permission)
                    lock.check_lock(json.loads(path.read_text()), source_document(), request_document(), "fixture-resolver")

    def test_publication_failure_preserves_readonly_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt.lock.json"
            original = b"old reviewed lock"
            path.write_bytes(original)
            path.chmod(0o444)
            failed = False
            # The writer performs its actual staging writes. Only the OS
            # publication operation fails; equivalent atomic implementations
            # that avoid it may succeed, but must then publish the full result.
            with patch.object(lock.os, "replace", side_effect=PermissionError("publication denied")):
                try:
                    lock.atomic_write(path, self.finalized())
                except Exception:
                    failed = True
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
            if failed:
                self.assertEqual(path.read_bytes(), original)
            else:
                lock.check_lock(json.loads(path.read_text()), source_document(), request_document(), "fixture-resolver")

    def test_unreachable_package_is_removed(self):
        candidate = graph()
        candidate["packages"]["/stable/orphan:amd64=9"] = package("orphan", "9")
        result = lock.finalize_result(candidate, graph(), source_document(), request_document(), "fixture-resolver")
        self.assertNotIn("/stable/orphan:amd64=9", result["packages"])

    def test_check_does_not_write_and_failed_update_preserves_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in [("sources.json", source_document()), ("requests.json", request_document()),
                               ("apt.lock.json", self.finalized())]:
                (root / name).write_text(json.dumps(data))
            before = (root / "apt.lock.json").read_bytes()
            (root / "apt.lock.json").chmod(0o640)
            before_mode = stat.S_IMODE((root / "apt.lock.json").stat().st_mode)
            command = [sys.executable, str(Path(lock.__file__)), "check", "--sources", str(root / "sources.json"),
                       "--requests", str(root / "requests.json"), "--lock", str(root / "apt.lock.json"),
                       "--resolver-version", "fixture-resolver"]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((root / "apt.lock.json").read_bytes(), before)
            self.assertEqual(stat.S_IMODE((root / "apt.lock.json").stat().st_mode), before_mode)
            candidate = graph()
            del candidate["packages"]["/stable/helper:amd64=2"]
            (root / "bad-export.json").write_text(json.dumps(candidate))
            command[2] = "update"
            result = subprocess.run(command + ["--resolved-lock", str(root / "bad-export.json")], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "apt.lock.json").read_bytes(), before)
            self.assertEqual(stat.S_IMODE((root / "apt.lock.json").stat().st_mode), before_mode)

    def test_fixed_archive_hash_is_checked(self):
        requests = request_document()
        requests["pinned_packages"] = [{"suite": "stable", "name": "demo", "architecture": "amd64", "version": "1", "sha256": "b" * 64}]
        with self.assertRaisesRegex(ValueError, "fixed DEB identity"):
            lock.finalize_result(graph(), graph(), source_document(), requests, "fixture-resolver")

    def test_suite_partitions_join_updates_without_mixing_distributions(self):
        sources = source_document()
        sources["sources"][0]["suites"] = ["stable", "stable-updates", "oldstable"]
        requests = request_document()
        requests["dependency_sets"].update({
            "updates": dict(requests["dependency_sets"]["runtime"], suites=["stable", "stable-updates"]),
            "oldtools": dict(requests["dependency_sets"]["runtime"], suites=["oldstable"]),
        })
        partitions = lock.resolution_partitions(sources, requests)
        self.assertEqual({frozenset(suites) for suites, _, _ in partitions},
                         {frozenset(["stable", "stable-updates"]), frozenset(["oldstable"])})
        for suites, selected_sources, selected_requests in partitions:
            self.assertEqual(set(lock.source_records(selected_sources)), suites)
            for request in selected_requests["dependency_sets"].values():
                self.assertLessEqual(set(request["suites"]), suites)

    def test_unscoped_request_retains_selection_from_all_configured_suites(self):
        sources = source_document()
        sources["sources"][0]["suites"].append("oldstable")
        requests = request_document()
        requests["dependency_sets"]["runtime"]["suites"] = []
        self.assertEqual(lock.resolution_partitions(sources, requests)[0][0], {"stable", "oldstable"})

    def test_unknown_request_suite_is_rejected_before_resolution(self):
        requests = request_document()
        requests["dependency_sets"]["runtime"]["suites"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "unknown source suites"):
            lock.resolution_partitions(source_document(), requests)

    def test_equal_package_versions_keep_distribution_hashes_and_native_filemaps(self):
        sources, requests, previous = source_document(), request_document(), graph()
        sources["sources"].append(dict(sources["sources"][0], suites=["oldstable"]))
        requests["dependency_sets"]["oldtools"] = dict(requests["dependency_sets"]["runtime"], suites=["oldstable"])
        calls = []

        def export(partition_sources, partition_requests, partition_previous, options):
            suites = set(lock.source_records(partition_sources))
            self.assertEqual(len(suites), 1, "mixed live indexes overwrite equal versions")
            suite = next(iter(suites))
            self.assertTrue(all(record["suite"] == suite for record in partition_previous["packages"].values()))
            calls.append(options.work_dir)
            result = graph()
            if suite == "oldstable":
                result = json.loads(json.dumps(result).replace("stable", "oldstable").replace('"runtime"', '"oldtools"'))
                for record in result["packages"].values():
                    record["sha256"] = "b" * 64
                    record["files"] = ["usr/include/oldstable/" + record["name"] + ".h"]
            result["_source_document"] = partition_sources
            result["_module_requests"] = partition_requests
            return result

        with tempfile.TemporaryDirectory() as directory, patch.object(lock, "resolve_partition", side_effect=export):
            candidate = lock.resolve(sources, requests, previous, SimpleNamespace(work_dir=directory))
        self.assertEqual(len(set(calls)), 2)
        result = lock.finalize_result(candidate, previous, sources, requests, "fixture-resolver")
        for key, record in previous["packages"].items():
            self.assertEqual(result["packages"][key], record)
        self.assertEqual(result["packages"]["/oldstable/demo:amd64=1"]["sha256"], "b" * 64)
        self.assertEqual(result["packages"]["/oldstable/demo:amd64=1"]["files"], ["usr/include/oldstable/demo.h"])
        self.assertEqual(set(result["_inputs"]["module_requests_document"]["dependency_sets"]), {"runtime", "oldtools"})
        lock.check_lock(result, sources, requests, "fixture-resolver")


class LockedAliasContractTest(unittest.TestCase):
    # Distroless's real Tk/BLT export has virtual and concrete references to
    # freetype with equal version/DEB SHA but different source names. The old
    # record, including its nonempty dependency graph, is the authority.
    def fixture(self):
        previous = graph()
        previous["sources"]["security"] = deepcopy(previous["sources"]["stable"])
        authority = "/stable/helper:amd64=2"
        previous["packages"][authority]["depends_on"] = ["/stable/leaf:amd64=1"]
        previous["packages"]["/stable/leaf:amd64=1"] = package("leaf")
        candidate = deepcopy(previous)
        alias = "/security/helper:amd64=2"
        candidate["packages"][alias] = dict(package("helper", "2"), suite="security")
        new = "/stable/tk:amd64=1"
        candidate["packages"][new] = package("tk", dependencies=[alias, authority])
        candidate["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/tk:amd64"] = "1"
        requests = request_document(["demo", "tk"])
        requests["dependency_sets"]["runtime"]["suites"] = ["stable", "security"]
        return candidate, previous, requests, alias, authority, new

    def test_new_root_reuses_complete_authority_and_is_idempotent(self):
        candidate, previous, requests, alias, authority, new = self.fixture()
        before = deepcopy(previous)
        # Simulate the export's incomplete record for the already locked DEB.
        candidate["packages"][authority]["depends_on"] = []
        result = lock.reuse_locked_aliases(candidate, previous, requests)
        self.assertEqual(result["packages"][new]["depends_on"], [authority, authority])
        for key, record in previous["packages"].items():
            self.assertEqual(result["packages"][key], record)
        self.assertEqual(result["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/demo:amd64"], "1")
        self.assertEqual(previous, before)
        self.assertEqual(lock.reuse_locked_aliases(result, previous, requests), result)
        lock.validate_lock(result)

    def test_sha_and_version_conflicts_are_rejected(self):
        for field, value in [("sha256", "b" * 64), ("version", "3")]:
            candidate, previous, requests, alias, _, new = self.fixture()
            candidate["packages"][alias][field] = value
            if field == "version":
                replacement = alias.rsplit("=", 1)[0] + "=3"
                candidate["packages"][replacement] = candidate["packages"].pop(alias)
                candidate["packages"][new]["depends_on"][0] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                lock.reuse_locked_aliases(candidate, previous, requests)

    def test_raw_export_repeated_after_new_root_is_locked_preserves_old_dictionary(self):
        raw, previous, requests, alias, authority, new = self.fixture()
        first = lock.reuse_locked_aliases(raw, previous, requests)
        del first["packages"][alias]  # The finalizer drops this unreachable alias.
        raw_again = deepcopy(raw)
        before = deepcopy(first)
        repeated = lock.reuse_locked_aliases(raw_again, first, requests)
        for key, record in first["packages"].items():
            self.assertEqual(repeated["packages"][key], record)
        self.assertEqual(repeated["dependency_sets"], first["dependency_sets"])
        self.assertEqual(first, before)
        self.assertEqual(raw_again, raw)

    def test_authorized_source_update_with_new_independent_root_keeps_cli_contract(self):
        previous = graph()
        candidate = deepcopy(previous)
        sources = source_document("https://new-snapshot.example/debian/20260801")
        candidate["sources"] = lock.source_records(sources)
        added = "/stable/added:amd64=1"
        candidate["packages"][added] = package("added")
        candidate["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/added:amd64"] = "1"
        requests = request_document(["demo", "added"])
        with self.assertRaisesRegex(ValueError, "allow-source-change"):
            lock.finalize_result(candidate, previous, sources, requests, "fixture")
        result = lock.finalize_result(candidate, previous, sources, requests, "fixture", allow_source_change=True)
        self.assertIn(added, result["packages"])

    def test_authorized_version_update_with_new_root_keeps_cli_contract(self):
        previous = graph()
        candidate = deepcopy(previous)
        changed = "/stable/helper:amd64=3"
        candidate["packages"][changed] = package("helper", "3")
        candidate["packages"]["/stable/demo:amd64=1"]["depends_on"] = [changed]
        added = "/stable/added:amd64=1"
        candidate["packages"][added] = package("added", dependencies=[changed])
        candidate["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/added:amd64"] = "1"
        requests = request_document(["demo", "added"])
        with self.assertRaisesRegex(ValueError, "version changed"):
            lock.finalize_result(candidate, previous, source_document(), requests, "fixture")
        result = lock.finalize_result(candidate, previous, source_document(), requests, "fixture", allow_version_change=True)
        self.assertIn(changed, result["packages"])

    def test_source_update_does_not_reuse_old_source_alias_authority(self):
        candidate, previous, requests, alias, authority, new = self.fixture()
        # Independent collections may deliberately use different origins.
        candidate["dependency_sets"]["runtime"]["sets"]["amd64"].pop("/stable/tk:amd64")
        candidate["dependency_sets"]["newtools"] = {"sets": {"amd64": {"/stable/tk:amd64": "1"}}}
        requests["dependency_sets"]["newtools"] = deepcopy(requests["dependency_sets"]["runtime"])
        candidate["packages"][new]["depends_on"] = [alias]
        candidate["sources"]["stable"]["uris"] = ["https://new-snapshot.example/debian/20260801"]
        result = lock.reuse_locked_aliases(candidate, previous, requests)
        self.assertEqual(result["packages"][new]["depends_on"], [alias])

    def test_requested_sources_and_authority_closure_are_enforced(self):
        candidate, previous, requests, _, _, _ = self.fixture()
        requests["dependency_sets"]["runtime"]["suites"] = ["stable"]
        with self.assertRaisesRegex(ValueError, "requested sources"):
            lock.reuse_locked_aliases(candidate, previous, requests)
        candidate, previous, requests, _, authority, _ = self.fixture()
        for document in [candidate, previous]:
            document["sources"]["bookworm"] = deepcopy(document["sources"]["stable"])
            document["packages"]["/bookworm/leaf:amd64=1"] = dict(package("leaf"), suite="bookworm")
            document["packages"][authority]["depends_on"] = ["/bookworm/leaf:amd64=1"]
        with self.assertRaisesRegex(ValueError, "requested sources"):
            lock.reuse_locked_aliases(candidate, previous, requests)

    def test_ambiguous_authority_and_unchanged_root_are_rejected(self):
        candidate, previous, requests, alias, authority, new = self.fixture()
        previous["packages"][alias] = deepcopy(candidate["packages"][alias])
        # Two same-content old authorities are not a basis for choosing one.
        other = "/stable-extra/helper:amd64=2"
        for document in [candidate, previous]:
            document["sources"]["stable-extra"] = deepcopy(document["sources"]["stable"])
            document["packages"][other] = dict(package("helper", "2"), suite="stable-extra")
        candidate["sources"]["third"] = deepcopy(candidate["sources"]["stable"])
        fresh = "/third/helper:amd64=2"
        candidate["packages"][fresh] = dict(package("helper", "2"), suite="third")
        candidate["packages"][new]["depends_on"][0] = fresh
        requests["dependency_sets"]["runtime"]["suites"] += ["stable-extra", "third"]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            lock.reuse_locked_aliases(candidate, previous, requests)
        candidate, previous, requests, alias, _, new = self.fixture()
        del candidate["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/tk:amd64"]
        extra = "/stable/extra:amd64=1"
        candidate["packages"][extra] = package("extra")
        candidate["packages"]["/stable/demo:amd64=1"]["depends_on"] += [alias, extra]
        with self.assertRaisesRegex(ValueError, "two versions or origins"):
            lock.reuse_locked_aliases(candidate, previous, requests)



class NewProviderAliasContractTest(unittest.TestCase):
    # Independent expectations come from the fixed DEB control, which has
    # Provides: libfreetype6-dev and six Depends groups. The original export
    # excerpt retains both real AMD64/ARM64 package identities and graph edges.
    def fixture(self):
        candidate = json.loads((Path(__file__).parent / "testdata/new-provider-alias-export.json").read_text())
        previous = dict(version=2, sources=deepcopy(candidate["sources"]), packages={}, dependency_sets={}, facts={})
        requests = {"dependency_sets": {"python_targets": {"suites": ["trixie", "trixie-security"]}}}
        return candidate, previous, requests

    def names(self, architecture):
        stem = "libfreetype-dev:" + architecture + "=2.13.3+dfsg-1+deb13u1"
        return "/trixie/" + stem, "/trixie-security/" + stem

    def test_original_export_preserves_six_real_dependency_groups_both_architectures(self):
        candidate, previous, requests = self.fixture()
        before = deepcopy(previous)
        result = lock.reuse_locked_aliases(candidate, previous, requests)
        for arch in ("amd64", "arm64"):
            alias, authority = self.names(arch)
            roots = result["dependency_sets"]["python_targets"]["sets"][arch]
            self.assertNotIn(alias.rsplit("=", 1)[0], roots)
            self.assertIn(authority.rsplit("=", 1)[0], roots)
            self.assertEqual(result["packages"][authority], candidate["packages"][authority])
            names = {result["packages"][key]["name"] for key in result["packages"][authority]["depends_on"]}
            for alternatives in [{"libfreetype6"}, {"libbrotli-dev"}, {"libbz2-dev"}, {"libc6-dev", "libc-dev"}, {"libpng-dev"}, {"zlib1g-dev", "libz-dev"}]:
                self.assertTrue(names & alternatives)
        self.assertEqual(previous, before)
        self.assertEqual(lock.reuse_locked_aliases(result, previous, requests), result)

    def test_partial_nonempty_alias_uses_same_strict_graph_contract(self):
        candidate, previous, requests = self.fixture()
        for arch in ("amd64", "arm64"):
            alias, authority = self.names(arch)
            candidate["packages"][alias]["depends_on"] = candidate["packages"][authority]["depends_on"][:1]
        result = lock.reuse_locked_aliases(candidate, previous, requests)
        self.assertEqual(len(result["dependency_sets"]["python_targets"]["sets"]["arm64"]), 1)

    def test_equal_or_incomparable_graphs_do_not_choose_an_origin(self):
        for kind in ("equal", "incomparable"):
            candidate, previous, requests = self.fixture()
            alias, authority = self.names("arm64")
            if kind == "equal":
                candidate["packages"][alias]["depends_on"] = candidate["packages"][authority]["depends_on"][:]
            else:
                candidate["packages"][alias]["depends_on"] = [self.names("amd64")[1]]
            with self.assertRaisesRegex(ValueError, "two versions or origins"):
                lock.reuse_locked_aliases(candidate, previous, requests)

    def test_different_binary_or_extraction_identity_remains_a_conflict(self):
        for field, value in [("sha256", "f" * 64), ("size", 1), ("section", "other"), ("files", ["different/file"])]:
            candidate, previous, requests = self.fixture()
            alias, _ = self.names("arm64")
            candidate["packages"][alias][field] = value
            with self.assertRaisesRegex(ValueError, "two versions or origins"):
                lock.reuse_locked_aliases(candidate, previous, requests)

    def test_different_version_remains_a_conflict(self):
        candidate, previous, requests = self.fixture()
        alias, _ = self.names("arm64")
        changed = alias.rsplit("=", 1)[0] + "=99"
        candidate["packages"][changed] = candidate["packages"].pop(alias)
        candidate["packages"][changed]["version"] = "99"
        candidate["dependency_sets"]["python_targets"]["sets"]["arm64"][alias.rsplit("=", 1)[0]] = "99"
        with self.assertRaisesRegex(ValueError, "two versions or origins"):
            lock.reuse_locked_aliases(candidate, previous, requests)

    def test_new_authority_and_its_closure_must_stay_in_requested_sources(self):
        candidate, previous, requests = self.fixture()
        requests["dependency_sets"]["python_targets"]["suites"] = ["trixie"]
        with self.assertRaisesRegex(ValueError, "requested sources"):
            lock.reuse_locked_aliases(candidate, previous, requests)

    def test_architecture_all_retains_native_coordinate_and_graph(self):
        candidate, previous, requests = self.fixture()
        amd = "/trixie/x11proto-core-dev:amd64=2024.1-1"
        arm = "/trixie/x11proto-core-dev:arm64=2024.1-1"
        pending, keys = [amd], set()
        while pending:
            key = pending.pop()
            if key not in keys:
                keys.add(key)
                pending.extend(candidate["packages"][key]["depends_on"])
        previous["packages"] = {key: deepcopy(candidate["packages"][key]) for key in keys}
        previous["dependency_sets"] = {"python_targets": {"sets": {"amd64": {amd.rsplit("=", 1)[0]: "2024.1-1"}}}}
        for architecture, key in [("amd64", amd), ("arm64", arm)]:
            candidate["dependency_sets"]["python_targets"]["sets"][architecture][key.rsplit("=", 1)[0]] = "2024.1-1"
        before = deepcopy(previous)
        result = lock.reuse_locked_aliases(candidate, previous, requests)
        self.assertIn(arm.rsplit("=", 1)[0], result["dependency_sets"]["python_targets"]["sets"]["arm64"])
        self.assertEqual(result["packages"][arm], candidate["packages"][arm])
        self.assertTrue(all(":arm64=" in key for key in result["packages"][arm]["depends_on"]))
        self.assertEqual(previous, before)
        for key, value in previous["packages"].items():
            self.assertEqual(result["packages"][key], value)

    def test_dangling_new_authority_is_rejected(self):
        candidate, previous, requests = self.fixture()
        _, authority = self.names("arm64")
        del candidate["packages"][candidate["packages"][authority]["depends_on"][0]]
        with self.assertRaisesRegex(ValueError, "missing from lock"):
            lock.reuse_locked_aliases(candidate, previous, requests)


class CollectionVersionContractTest(unittest.TestCase):
    def test_single_collection_rejects_conflicting_transitive_version(self):
        document = graph()
        document["packages"]["/stable/helper:amd64=1"] = package("helper", "1")
        document["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/helper:amd64"] = "1"
        with self.assertRaisesRegex(ValueError, "two versions or origins of helper"):
            lock.validate_lock(document)
        with self.assertRaisesRegex(ValueError, "two versions or origins of helper"):
            lock.finalize_result(document, graph(), source_document(), request_document(), "fixture")

    def test_cross_architecture_dependency_keeps_its_own_binary_identity(self):
        document = graph()
        foreign = "/stable/helper:arm64=2"
        document["packages"][foreign] = package("helper", "2", "arm64")
        document["packages"]["/stable/demo:amd64=1"]["depends_on"].append(foreign)
        lock.validate_lock(document)

    def test_same_architecture_conflicting_origin_is_rejected(self):
        document = graph()
        document["sources"]["vendor"] = deepcopy(document["sources"]["stable"])
        foreign = "/vendor/helper:amd64=2"
        document["packages"][foreign] = dict(package("helper", "2"), suite="vendor")
        document["packages"]["/stable/demo:amd64=1"]["depends_on"].append(foreign)
        with self.assertRaisesRegex(ValueError, "two versions or origins of helper"):
            lock.validate_lock(document)

    def test_architecture_all_aliases_are_one_actual_package(self):
        document = graph()
        universal = package("helper", "2", "all")
        document["packages"]["/stable/helper:amd64=2"] = universal
        alias = "/stable/helper:arm64=2"
        document["packages"][alias] = deepcopy(universal)
        document["packages"]["/stable/demo:amd64=1"]["depends_on"].append(alias)
        lock.validate_lock(document)

    def test_architecture_all_conflicts_with_native_binary(self):
        document = graph()
        universal = "/stable/helper:all=2"
        document["packages"][universal] = package("helper", "2", "all")
        document["packages"]["/stable/demo:amd64=1"]["depends_on"].append(universal)
        with self.assertRaisesRegex(ValueError, "two versions or origins of helper"):
            lock.validate_lock(document)

    def test_independent_collections_can_keep_distinct_versions(self):
        document = graph()
        document["packages"]["/stable/helper:amd64=1"] = package("helper", "1")
        document["dependency_sets"]["other"] = {"sets": {"amd64": {"/stable/helper:amd64": "1"}}}
        lock.validate_lock(document)


class NamedOriginContractTest(unittest.TestCase):
    def sources(self):
        return {"version": 1, "sources": [
            {"uris": ["https://debian.example"], "suites": ["trixie"], "components": ["main"], "architectures": ["amd64"]},
            {"origin": "docker", "suite_keys": {"trixie": "docker-trixie"}, "uris": ["https://docker.example"], "suites": ["trixie"], "components": ["stable"], "architectures": ["amd64"]},
        ]}

    def test_same_actual_suite_retains_independent_download_origins(self):
        records = lock.source_records(self.sources())
        self.assertEqual(records["trixie"], {"uris": ["https://debian.example"], "components": ["main"], "architectures": ["amd64"], "types": []})
        self.assertEqual(records["docker-trixie"]["suite"], "trixie")
        self.assertEqual(records["docker-trixie"]["origin"], "docker")
        self.assertEqual(records["docker-trixie"]["uris"], ["https://docker.example"])

    def test_explicit_suite_mapping_drives_joint_resolution(self):
        requests = {"version": 1, "dependency_sets": {"host": {"packages": ["docker-ce:amd64 (= 5:28.5.2-1~debian.13~trixie)"], "suites": ["trixie", "docker-trixie"]}}}
        partitions = lock.resolution_partitions(self.sources(), requests)
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0][1], self.sources())
        self.assertEqual(partitions[0][2], requests)

    def test_colliding_logical_key_fails(self):
        document = self.sources()
        document["sources"][1]["suite_keys"]["trixie"] = "trixie"
        with self.assertRaisesRegex(ValueError, "conflicting source"):
            lock.source_records(document)

    def test_mapping_requires_actual_suite_and_named_origin(self):
        for aliases, origin in [({"bookworm": "vendor"}, "docker"), ({"trixie": "vendor"}, "")]:
            document = self.sources(); document["sources"][1].update(suite_keys=aliases, origin=origin)
            with self.assertRaises(ValueError): lock.source_records(document)


if __name__ == "__main__":
    unittest.main()
