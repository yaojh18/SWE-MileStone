from __future__ import annotations

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from capture_dubbo_node_overlays import (
    CaptureError,
    CaptureSource,
    DockerInstruction,
    EndpointSpec,
    RawCapture,
    binary_patch,
    build_docker_replay_script,
    build_replay_and_capture_script,
    changed_paths,
    endpoint_fingerprint,
    find_sif,
    import_archive_tree,
    is_pure_maven_build,
    load_endpoints,
    load_sif_manifest,
    maybe_write_aggregate_manifest,
    materialize_endpoint,
    parse_dockerfile,
    resolve_post_hoist,
    reusable_manifest,
    shard_endpoints,
)


def command(*args: str, cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def make_tree_archive(path: Path, files: dict[str, bytes | None]) -> None:
    """Create a Git-archive-like tar; None values represent absent paths."""

    with tarfile.open(path, "w:") as handle:
        directories: set[str] = set()
        for name, payload in files.items():
            if payload is None:
                continue
            parent = Path(name).parent
            while str(parent) not in {"", "."}:
                directories.add(parent.as_posix())
                parent = parent.parent
        for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
            info = tarfile.TarInfo(directory + "/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            handle.addfile(info)
        for name, payload in sorted(files.items()):
            if payload is None:
                continue
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))


class CaptureDubboNodeOverlaysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "Test", cwd=self.repo)
        command("git", "config", "user.email", "test@example.invalid", cwd=self.repo)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "Feature.java").write_text("class Feature {}\n")
        (self.repo / "obsolete.txt").write_text("delete me\n")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "post hoist start", cwd=self.repo)
        self.start = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M001-start", cwd=self.repo)

        (self.repo / "src" / "Feature.java").write_text("class Feature { int end = 1; }\n")
        command("git", "add", "-A", cwd=self.repo)
        command("git", "commit", "-qm", "post hoist end", cwd=self.repo)
        self.end = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command("git", "tag", "milestone-M001-end", cwd=self.repo)
        self.output = self.root / "output"
        self.scratch = self.root / "scratch"
        self.output.mkdir()
        self.scratch.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_all_26_milestones_and_both_endpoints(self) -> None:
        rows = []
        for index in range(26):
            milestone = f"M{index:03d}"
            rows.append(
                {
                    "id": milestone,
                    "tag_name_start": f"milestone-{milestone}-start",
                    "commit_sha_start": "a" * 40,
                    "tag_name_end": f"milestone-{milestone}-end",
                    "commit_sha_end": "b" * 40,
                }
            )
        metadata = self.root / "metadata.json"
        metadata.write_text(json.dumps({"milestones": rows}))
        endpoints = load_endpoints(metadata)
        self.assertEqual(len(endpoints), 52)
        self.assertEqual(endpoints[0].node_id, "M000:start")
        self.assertEqual(endpoints[-1].node_id, "M025:end")

        rows[-1]["id"] = rows[0]["id"]
        metadata.write_text(json.dumps({"milestones": rows}))
        with self.assertRaisesRegex(CaptureError, "duplicate milestone"):
            load_endpoints(metadata)

    def test_resolves_post_hoist_tag_instead_of_declared_sha(self) -> None:
        endpoint = EndpointSpec(
            "M001", "start", "milestone-M001-start", "f" * 40
        )
        sha, tree = resolve_post_hoist(self.repo, endpoint)
        self.assertEqual(sha, self.start)
        self.assertEqual(
            tree, command("git", "rev-parse", f"{self.start}^{{tree}}", cwd=self.repo)
        )

    def test_dockerfile_parser_preserves_heredoc_and_continuations(self) -> None:
        dockerfile = r'''FROM ignored
ENV FOO=bar PATH="${FOO}:${PATH}"
WORKDIR /testbed
RUN cat > /usr/local/bin/apply_patches.sh << 'SCRIPT'
#!/bin/bash
echo patched > marker.txt
SCRIPT
RUN chmod +x /usr/local/bin/apply_patches.sh
RUN cd /testbed && \
    mvn clean install -DskipTests -Pskip-spotless
RUN cd /testbed && mvn spotless:apply -q || true
'''
        instructions = parse_dockerfile(dockerfile)
        self.assertEqual(
            [item.kind for item in instructions],
            ["FROM", "ENV", "WORKDIR", "RUN", "RUN", "RUN", "RUN"],
        )
        self.assertIn("echo patched", instructions[3].value)
        script = build_docker_replay_script(instructions)
        self.assertIn("apply_patches.sh << 'SCRIPT'", script)
        self.assertIn("skipped pure Maven build", script)
        self.assertNotIn("mvn clean install", script)
        self.assertIn("mvn spotless:apply", script)
        self.assertIn('export PATH="${FOO}:${PATH}"', script)
        check = self.root / "replay.sh"
        check.write_text(script)
        subprocess.check_call(["bash", "-n", str(check)])

    def test_only_standalone_non_spotless_maven_is_skipped(self) -> None:
        self.assertTrue(is_pure_maven_build("cd /testbed && mvn install -DskipTests"))
        self.assertTrue(is_pure_maven_build("./mvnw test-compile -DskipTests"))
        self.assertFalse(is_pure_maven_build("mvn spotless:apply -q || true"))
        self.assertFalse(
            is_pure_maven_build("sed -i s/a/b/ pom.xml && mvn install -DskipTests")
        )

    def test_replay_capture_script_is_shell_valid_and_exports_both_roles(self) -> None:
        replay = build_docker_replay_script(
            [
                DockerInstruction("WORKDIR", "/testbed", 1),
                DockerInstruction("RUN", "echo ready > /tmp/ready", 2),
            ]
        )
        combined = build_replay_and_capture_script(
            replay,
            [
                EndpointSpec("M001", "start", "milestone-M001-start", "a"),
                EndpointSpec("M001", "end", "milestone-M001-end", "b"),
            ],
        )
        path = self.root / "combined.sh"
        path.write_text(combined)
        subprocess.check_call(["bash", "-n", str(path)])
        self.assertIn("capture_endpoint milestone-M001-start start", combined)
        self.assertIn("capture_endpoint milestone-M001-end end", combined)

    def test_materializes_deletion_aware_overlay_and_resumes(self) -> None:
        endpoint = EndpointSpec(
            "M001", "start", "milestone-M001-start", "f" * 40
        )
        post_sha, post_tree = resolve_post_hoist(self.repo, endpoint)
        archive1 = self.root / "capture1.tar"
        archive2 = self.root / "capture2.tar"
        effective_files = {
            "src/Feature.java": b"class Feature { int patched = 2; }\n",
            "new.txt": b"new\n",
            # obsolete.txt is deliberately absent and must be deleted.
        }
        make_tree_archive(archive1, effective_files)
        make_tree_archive(archive2, effective_files)
        source_file = self.root / "m001.sif"
        source_file.write_bytes(b"fake-sif")
        source = CaptureSource("milestone_sif", source_file, "0" * 64)
        fingerprint = endpoint_fingerprint(
            endpoint,
            post_sha,
            post_tree,
            source,
            base_sif_digest=None,
            program_digest="1" * 64,
        )
        manifest = materialize_endpoint(
            endpoint,
            base_repo=self.repo,
            post_hoist_sha=post_sha,
            post_hoist_tree=post_tree,
            captures=(
                RawCapture(archive1, {"attempt": 1}),
                RawCapture(archive2, {"attempt": 2}),
            ),
            source=source,
            input_fingerprint=fingerprint,
            output_root=self.output,
            scratch=self.scratch,
        )
        self.assertTrue(manifest["repeat_capture_match"])
        self.assertTrue(manifest["patch_reconstruction"]["ok"])
        self.assertEqual(
            manifest["overlay_changed_paths"],
            ["new.txt", "obsolete.txt", "src/Feature.java"],
        )
        patch_path = self.output / "M001__start" / manifest["effective.patch"]["path"]
        patch = patch_path.read_bytes()
        self.assertIn(b"obsolete.txt", patch)
        self.assertIn(b"new.txt", patch)
        self.assertEqual(manifest["effective.patch"]["sha256"], __import__("hashlib").sha256(patch).hexdigest())

        reused = reusable_manifest(
            self.output / "M001__start" / "manifest.json",
            fingerprint,
            self.output,
        )
        self.assertIsNotNone(reused)
        self.assertIsNone(
            reusable_manifest(
                self.output / "M001__start" / "manifest.json",
                "changed",
                self.output,
            )
        )

    def test_repeat_tree_mismatch_fails_closed(self) -> None:
        endpoint = EndpointSpec(
            "M001", "start", "milestone-M001-start", "f" * 40
        )
        post_sha, post_tree = resolve_post_hoist(self.repo, endpoint)
        first = self.root / "first.tar"
        second = self.root / "second.tar"
        make_tree_archive(first, {"src/Feature.java": b"one\n"})
        make_tree_archive(second, {"src/Feature.java": b"two\n"})
        source = CaptureSource("milestone_sif", self.root / "x.sif", "0" * 64)
        with self.assertRaisesRegex(CaptureError, "fresh captures disagree"):
            materialize_endpoint(
                endpoint,
                base_repo=self.repo,
                post_hoist_sha=post_sha,
                post_hoist_tree=post_tree,
                captures=(RawCapture(first, {}), RawCapture(second, {})),
                source=source,
                input_fingerprint="fingerprint",
                output_root=self.output,
                scratch=self.scratch,
            )

    def test_archive_import_preserves_exact_tree_and_patch_helpers(self) -> None:
        archive = self.root / "tree.tar"
        make_tree_archive(archive, {"only.txt": b"only\n"})
        tree = import_archive_tree(
            self.repo, self.start, archive, self.scratch / "import"
        )
        self.assertEqual(
            changed_paths(self.repo, self.start, tree),
            ["obsolete.txt", "only.txt", "src/Feature.java"],
        )
        patch = binary_patch(self.repo, self.start, tree)
        self.assertIn(b"only.txt", patch)

    def test_sif_discovery_prefers_existing_unambiguous_candidate(self) -> None:
        image_root = self.root / "images"
        image_root.mkdir()
        image = image_root / "m001.1.sif"
        image.write_bytes(b"image")
        self.assertEqual(find_sif(image_root, "M001.1"), image)
        self.assertIsNone(find_sif(image_root, "M999"))

    def test_sif_manifest_maps_case_insensitively_with_exact_workspace(self) -> None:
        image_root = self.root / "images"
        image_root.mkdir()
        manifest = self.root / "images.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "workspace": "dubbo-workspace",
                    "milestone_id": "m001.1",
                    "destination_rel": "dubbo-workspace/m001.1.sif",
                    "source": "docker://example/dubbo:m001.1",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "workspace": "some-other-workspace",
                    "milestone_id": "m001.1",
                    "destination_rel": "some-other-workspace/m001.1.sif",
                }
            )
            + "\n"
        )
        records = load_sif_manifest(
            manifest, workspace="dubbo-workspace", sif_root=image_root
        )
        self.assertEqual(set(records), {"m001.1"})
        path, record = records["M001.1".casefold()]
        self.assertEqual(path, image_root / "m001.1.sif")
        self.assertEqual(record["milestone_id"], "m001.1")

    def test_stable_rank_sharding_by_milestone_or_endpoint(self) -> None:
        endpoints = [
            EndpointSpec(mid, role, f"milestone-{mid}-{role}", "")
            for mid in ("M001", "M002", "M003")
            for role in ("start", "end")
        ]
        rank0 = shard_endpoints(
            endpoints, rank=0, world=2, shard_by="milestone"
        )
        rank1 = shard_endpoints(
            endpoints, rank=1, world=2, shard_by="milestone"
        )
        self.assertEqual(
            [item.node_id for item in rank0],
            ["M001:start", "M001:end", "M003:start", "M003:end"],
        )
        self.assertEqual(
            [item.node_id for item in rank1], ["M002:start", "M002:end"]
        )
        endpoint_rank0 = shard_endpoints(
            endpoints, rank=0, world=2, shard_by="endpoint"
        )
        self.assertEqual(
            [item.node_id for item in endpoint_rank0],
            ["M001:start", "M002:start", "M003:start"],
        )
        with self.assertRaisesRegex(CaptureError, "invalid rank/world"):
            shard_endpoints(endpoints, rank=2, world=2, shard_by="milestone")

    def test_last_rank_can_publish_aggregate_manifest(self) -> None:
        fingerprint = "run-fingerprint"
        for rank, paths in enumerate(
            (["M001__start/manifest.json"], ["M001__end/manifest.json"])
        ):
            (self.output / f"summary.rank{rank}.json").write_text(
                json.dumps(
                    {
                        "rank": rank,
                        "world": 2,
                        "run_fingerprint": fingerprint,
                        "status": "complete",
                        "resumed_endpoint_count": rank,
                        "endpoint_manifests": paths,
                        "errors": [],
                    }
                )
            )
        aggregate = maybe_write_aggregate_manifest(
            self.output,
            world=2,
            run_fingerprint=fingerprint,
            expected_endpoint_count=2,
        )
        self.assertIsNotNone(aggregate)
        assert aggregate is not None
        self.assertEqual(aggregate["status"], "complete")
        self.assertEqual(aggregate["resumed_endpoint_count"], 1)
        self.assertTrue((self.output / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
