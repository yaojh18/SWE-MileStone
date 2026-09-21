from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PIPELINE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(PIPELINE_ROOT.parent))

from agent_pipeline.build_task_manifests import load_sif_manifest  # noqa: E402
from agent_pipeline.build_views import load_partition_patch  # noqa: E402
from agent_pipeline.materialize_final_dataset import (  # noqa: E402
    CLASSIFICATION_CATEGORIES,
    materialize_final_dataset,
)


WORKSPACE = "example_org_example_repo_v1_v2"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make_dataset(root: Path) -> Path:
    dataset = root / "merged"
    repo = dataset / WORKSPACE
    repo.mkdir(parents=True)
    fields = [
        "id", "title", "category", "commits", "integration_test_commit", "mini_srs",
        "confidence_score", "src_loc", "start_time", "end_time", "loc", "additions",
        "deletions", "src_additions", "src_deletions", "touched_test_files", "touched_src_files",
    ]
    rows = []
    for milestone_id in ("A", "M100", "B"):
        rows.append(
            {
                "id": milestone_id,
                "title": f"Title {milestone_id}",
                "category": "Feature",
                "commits": milestone_id.lower() * 7,
                "integration_test_commit": "",
                "mini_srs": f"Implement {milestone_id}.",
                "confidence_score": "0.9",
                "src_loc": "420" if milestone_id == "M100" else "100",
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-02T00:00:00Z",
                "loc": "420" if milestone_id == "M100" else "100",
                "additions": "420" if milestone_id == "M100" else "100",
                "deletions": "0",
                "src_additions": "420" if milestone_id == "M100" else "100",
                "src_deletions": "0",
                "touched_test_files": "",
                "touched_src_files": "src/example.py",
            }
        )
    write_csv(repo / "milestones.csv", fields, rows)
    dep_fields = ["source_id", "target_id", "type", "strength", "rationale", "confidence_score"]
    write_csv(
        repo / "dependencies.csv",
        dep_fields,
        [
            {"source_id": "A", "target_id": "M100", "type": "FUNC", "strength": "Strong", "rationale": "A first", "confidence_score": "0.9"},
            {"source_id": "M100", "target_id": "B", "type": "FUNC", "strength": "Strong", "rationale": "B later", "confidence_score": "0.9"},
        ],
    )
    (repo / "selected_milestone_ids.txt").write_text("A\nM100\nB\n", encoding="utf-8")
    metadata = {
        "total_milestones": 3,
        "topological_order": {"full_order": ["A", "M100", "B"], "independent_milestones": ["A"]},
        "milestones": [],
    }
    for milestone_id in ("A", "M100", "B"):
        metadata["milestones"].append(
            {
                "id": milestone_id,
                "title": f"Title {milestone_id}",
                "commits_count": 1,
                "commits": milestone_id.lower() * 7,
                "base_commit": "0" * 40,
                "tag_name_start": f"milestone-{milestone_id}-start",
                "commit_sha_start": f"{milestone_id.lower()}" * 40,
                "tag_name_end": f"milestone-{milestone_id}-end",
                "commit_sha_end": f"{milestone_id.lower()}" * 40,
                "parent_milestones": [] if milestone_id == "A" else (["A"] if milestone_id == "M100" else ["M100"]),
            }
        )
        (repo / "srs" / milestone_id).mkdir(parents=True)
        (repo / "srs" / milestone_id / "SRS.md").write_text(f"# {milestone_id}\n", encoding="utf-8")
        (repo / "test_results" / milestone_id).mkdir(parents=True)
        empty_section = {category: [] for category in CLASSIFICATION_CATEGORIES}
        write_json(
            repo / "test_results" / milestone_id / f"{milestone_id}_classification.json",
            {
                "summary": {},
                "classification": empty_section,
                "stable_classification": empty_section,
            },
        )
        (repo / "dockerfiles" / milestone_id).mkdir(parents=True)
    write_json(repo / "metadata.json", metadata)
    write_json(dataset / "merge_manifest.json", {"schema_version": 1})
    return dataset


def make_run(root: Path) -> tuple[Path, Path]:
    runs = root / "runs"
    run = runs / WORKSPACE / "M100"
    view_dir = run / "view"
    view_dir.mkdir(parents=True)
    view = json.loads((FIXTURES / "partition_view.json").read_text(encoding="utf-8"))
    output = json.loads((FIXTURES / "partition_output_valid.json").read_text(encoding="utf-8"))
    units = [
        {"unit_id": "u-001", "source_loc": 140, "path": "src/alpha.py", "is_test_path": False, "additions": 140, "deletions": 0, "changed_loc": 140, "oversized_atomic_unit": False, "diff": "diff --git a/src/alpha.py b/src/alpha.py\n--- a/src/alpha.py\n+++ b/src/alpha.py\n@@ -0,0 +1 @@\n+alpha\n"},
        {"unit_id": "u-002", "source_loc": 160, "path": "src/alpha_api.py", "is_test_path": False, "additions": 160, "deletions": 0, "changed_loc": 160, "oversized_atomic_unit": False, "diff": "diff --git a/src/alpha_api.py b/src/alpha_api.py\n--- a/src/alpha_api.py\n+++ b/src/alpha_api.py\n@@ -0,0 +1 @@\n+api\n"},
        {"unit_id": "u-003", "source_loc": 120, "path": "src/beta.py", "is_test_path": False, "additions": 120, "deletions": 0, "changed_loc": 120, "oversized_atomic_unit": False, "diff": "diff --git a/src/beta.py b/src/beta.py\n--- a/src/beta.py\n+++ b/src/beta.py\n@@ -0,0 +1 @@\n+beta\n"},
    ]
    (view_dir / "change_units.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in units), encoding="utf-8"
    )
    (view_dir / "tests.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in view["tests"]), encoding="utf-8"
    )
    write_json(
        view_dir / "input.json",
        {
            "schema_version": 1,
            "task_kind": "partition",
            "workspace": WORKSPACE,
            "milestone_id": "M100",
            "input_hash": "a" * 64,
        },
    )
    original_patch = "".join(item["diff"] for item in units)
    write_json(
        view_dir / "patch_manifest.json",
        {"original_patch_sha256": hashlib.sha256(original_patch.encode()).hexdigest()},
    )
    write_json(run / "artifacts" / "manifest.json", output)
    write_json(run / "validation.json", {"valid": True, "errors": [], "warnings": [], "metrics": {}})
    write_json(
        run / "run_manifest.json",
        {"task_kind": "partition", "workspace": WORKSPACE, "milestone_id": "M100", "validation_valid": True},
    )
    (run / "COMPLETE").touch()
    tasks = root / "partition_tasks.jsonl"
    tasks.write_text(json.dumps({"workspace": WORKSPACE, "milestone_id": "M100"}) + "\n", encoding="utf-8")
    return runs, tasks


def make_sif_manifest(root: Path) -> Path:
    path = root / "sif.jsonl"
    records = [
        {
            "workspace": WORKSPACE,
            "milestone_id": milestone_id.lower(),
            "source": f"docker://example/{milestone_id.lower()}",
            "destination_rel": f"{WORKSPACE}/{milestone_id.lower()}.sif",
        }
        for milestone_id in ("A", "M100", "B")
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    return path


def make_curator_sif_manifest(root: Path) -> Path:
    path = root / "curator-sif.jsonl"
    record = {
        "index": 0,
        "workspace": WORKSPACE,
        "milestone_id": "base-offline",
        "image_kind": "repository_curator_base_offline",
        "not_evaluator_authority": True,
        "source": "docker://example/base-offline-v0.9",
        "destination_rel": f"{WORKSPACE}/base-offline.sif",
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


class FinalDatasetMaterializationTests(unittest.TestCase):
    def test_end_to_end_rewrites_dag_and_quality_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = make_dataset(root)
            runs, tasks = make_run(root)
            sif = make_sif_manifest(root)
            curator_sif = make_curator_sif_manifest(root)
            output = root / "final"
            result = materialize_final_dataset(
                merged_dataset=dataset,
                partition_tasks=tasks,
                partition_runs=runs,
                sif_manifest=sif,
                curator_sif_manifest=curator_sif,
                output_dataset=output,
                expected_partitions=1,
            )
            self.assertEqual(result["quality_task_count"], 4)
            repo = output / WORKSPACE
            dag = json.loads((repo / "dag" / "final_dag.json").read_text(encoding="utf-8"))
            edges = {(item["source_id"], item["target_id"]) for item in dag["edges"]}
            self.assertEqual(
                edges,
                {("A", "M100.sub-01"), ("M100.sub-01", "M100.sub-02"), ("M100.sub-02", "B")},
            )
            self.assertFalse((repo / "srs" / "M100").exists())
            self.assertTrue((repo / "partition_sources" / "M100" / "original_dataset_artifacts" / "srs" / "SRS.md").is_file())
            classification = json.loads(
                (repo / "test_results" / "M100.sub-02" / "M100.sub-02_classification.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(classification["stable_classification"]), set(CLASSIFICATION_CATEGORIES))
            provenance = json.loads((repo / "partition_provenance" / "M100.sub-01.json").read_text(encoding="utf-8"))
            units, patch, _, _ = load_partition_patch(repo, provenance)
            self.assertEqual([unit["unit_id"] for unit in units], ["u-001", "u-002"])
            self.assertEqual(hashlib.sha256(patch.encode()).hexdigest(), json.loads((repo / "patches" / "M100.sub-01" / "patch_manifest.json").read_text(encoding="utf-8"))["gold_patch_sha256"])
            quality = [
                json.loads(line)
                for line in (output / "agent_pipeline_manifests" / "test_quality" / "test_quality_tasks.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            subtask_record = next(item for item in quality if item["milestone_id"] == "M100.sub-01")
            self.assertEqual(subtask_record["docker_source_id"], "M100")
            self.assertEqual(subtask_record["sif_role"], "repository_curator_base_offline")
            self.assertEqual(subtask_record["sif_manifest_record"]["milestone_id"], "base-offline")
            self.assertEqual(
                subtask_record["evaluator_sif_manifest_record"]["milestone_id"],
                "m100",
            )
            validator = PIPELINE_ROOT.parent / "SWE-Milestone-data-repartitioned" / "scripts" / "validate_data.py"
            checked = subprocess.run(
                [sys.executable, str(validator), "--data-root", str(output), "--skip-readme", "--json"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_invalid_saved_validation_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = make_dataset(root)
            runs, tasks = make_run(root)
            sif = make_sif_manifest(root)
            validation = runs / WORKSPACE / "M100" / "validation.json"
            write_json(validation, {"valid": False})
            output = root / "final"
            with self.assertRaisesRegex(ValueError, "saved validation"):
                materialize_final_dataset(
                    merged_dataset=dataset,
                    partition_tasks=tasks,
                    partition_runs=runs,
                    sif_manifest=sif,
                    output_dataset=output,
                    expected_partitions=1,
                )
            self.assertFalse(output.exists())

    def test_sif_casefold_collision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sif.jsonl"
            records = [
                {"workspace": WORKSPACE, "milestone_id": "M100", "source": "a", "destination_rel": "a.sif"},
                {"workspace": WORKSPACE, "milestone_id": "m100", "source": "b", "destination_rel": "b.sif"},
            ]
            path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "case-folding.*collision"):
                load_sif_manifest(path)


if __name__ == "__main__":
    unittest.main()
