#!/usr/bin/env python3
"""Audit SWE-Milestone dataset DAG, task, and Docker declarations.

This script is intentionally offline. It cross-checks the downloaded dataset
against the official harness's pull_images.sh manifest, but it does not contact
Docker Hub or start containers.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def read_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def git_head(path: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()


def parse_image_manifest(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    project_root = path.resolve().parents[1]
    quarantine_names = {
        item.stem.lower()
        for item in (project_root / "quarantine_configs").glob("*.yaml")
    }
    full_names = dict(
        re.findall(r'^REPO_FULL\[([^]]+)\]="([^"]+)"$', text, re.MULTILINE)
    )
    milestone_lists = dict(
        re.findall(r'^REPO_MIDS\[([^]]+)\]="([^"]*)"$', text, re.MULTILINE)
    )
    result: dict[str, dict[str, Any]] = {}
    for short_name, full_name in full_names.items():
        mids = milestone_lists.get(short_name, "").split()
        quarantine_enabled = full_name.lower() in quarantine_names
        result[full_name.lower()] = {
            "short_name": short_name,
            "full_name": full_name,
            "milestone_ids": mids,
            "milestone_ids_lower": {item.lower() for item in mids},
            "hub_base_image": f"hyd2apse/{short_name}:base-v0.9",
            "local_base_image": f"{full_name.lower()}/base:v0.9",
            "hub_base_offline_image": f"hyd2apse/{short_name}:base-offline-v0.9",
            "local_base_offline_image": f"{full_name.lower()}/base-offline:v0.9",
            "quarantine_enabled": quarantine_enabled,
            "standard_hub_agent_image": (
                f"hyd2apse/{short_name}:base-offline-v0.9"
                if quarantine_enabled
                else f"hyd2apse/{short_name}:base-v0.9"
            ),
            "standard_local_agent_image": (
                f"{full_name.lower()}/base-offline:v0.9"
                if quarantine_enabled
                else f"{full_name.lower()}/base:v0.9"
            ),
        }
    return result


def edge_pairs(rows: Iterable[dict[str, str]]) -> list[tuple[str, str]]:
    result = []
    for row in rows:
        source = (row.get("source_id") or "").strip()
        target = (row.get("target_id") or "").strip()
        if source and target:
            result.append((source, target))
    return result


def topo_order(nodes: set[str], edges: set[tuple[str, str]]) -> list[str]:
    indegree = {node: 0 for node in nodes}
    successors: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if source in nodes and target in nodes:
            successors[source].append(target)
            indegree[target] += 1
    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        newly_ready = []
        for target in successors[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                newly_ready.append(target)
        if newly_ready:
            ready = sorted(ready + newly_ready)
    if len(order) != len(nodes):
        raise ValueError(f"cycle detected: ordered {len(order)} of {len(nodes)} nodes")
    return order


def weak_components(nodes: set[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    adjacent: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        if source in nodes and target in nodes:
            adjacent[source].add(target)
            adjacent[target].add(source)
    unseen = set(nodes)
    components = []
    while unseen:
        start = min(unseen)
        queue = deque([start])
        unseen.remove(start)
        component = []
        while queue:
            node = queue.popleft()
            component.append(node)
            for neighbor in sorted(adjacent[node]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    return sorted(components, key=lambda item: (-len(item), item))


def find_classification(repo_dir: Path, milestone_id: str) -> Path:
    return (
        repo_dir
        / "test_results"
        / milestone_id
        / f"{milestone_id}_classification.json"
    )


def find_filter(repo_dir: Path, milestone_id: str) -> Path:
    return (
        repo_dir
        / "test_results"
        / milestone_id
        / f"{milestone_id}_filter_list.json"
    )


def audit_repo(
    repo_dir: Path,
    image_manifest: dict[str, dict[str, Any]],
    dockerhub_cache_dir: Path | None = None,
) -> dict[str, Any]:
    milestones = read_csv(repo_dir / "milestones.csv")
    catalog_ids = {row["id"].strip() for row in milestones if row.get("id", "").strip()}
    selected_path = repo_dir / "selected_milestone_ids.txt"
    selected_ids = read_ids(selected_path)
    active_ids = selected_ids if selected_ids else catalog_ids
    nongraded_ids = read_ids(repo_dir / "non-graded_milestone_ids.txt")
    graded_ids = active_ids - nongraded_ids
    inactive_ids = catalog_ids - active_ids

    base_edges_all = set(edge_pairs(read_csv(repo_dir / "dependencies.csv")))
    additional_edges_all = set(
        edge_pairs(read_csv(repo_dir / "additional_dependencies.csv"))
    )
    base_edges = {
        edge for edge in base_edges_all if edge[0] in active_ids and edge[1] in active_ids
    }
    additional_edges = {
        edge
        for edge in additional_edges_all
        if edge[0] in active_ids and edge[1] in active_ids
    }
    effective_edges = base_edges | additional_edges
    effective_order = topo_order(active_ids, effective_edges)

    metadata = json.loads((repo_dir / "metadata.json").read_text(encoding="utf-8"))
    repo_name = metadata.get("repo_name")
    image_info = image_manifest.get(repo_dir.name.lower())
    declared_image_ids = image_info["milestone_ids_lower"] if image_info else set()
    active_lower = {item.lower() for item in active_ids}

    missing_srs = sorted(
        mid for mid in active_ids if not (repo_dir / "srs" / mid / "SRS.md").is_file()
    )
    missing_classification = sorted(
        mid for mid in active_ids if not find_classification(repo_dir, mid).is_file()
    )
    nodes_with_filter = sorted(
        mid for mid in active_ids if find_filter(repo_dir, mid).is_file()
    )
    catalog_missing_srs = sorted(
        mid for mid in catalog_ids if not (repo_dir / "srs" / mid / "SRS.md").is_file()
    )
    catalog_missing_classification = sorted(
        mid for mid in catalog_ids if not find_classification(repo_dir, mid).is_file()
    )
    inactive_with_srs = sorted(
        mid for mid in inactive_ids if (repo_dir / "srs" / mid / "SRS.md").is_file()
    )
    inactive_with_classification = sorted(
        mid for mid in inactive_ids if find_classification(repo_dir, mid).is_file()
    )

    # Reproduce run_e2e --milestones ordering: it considers only dependencies.csv,
    # not additional_dependencies.csv. Check whether each resulting prefix is also
    # dependency-closed under the effective DAG used by the orchestrator.
    harness_prefix_order = topo_order(active_ids, base_edges)
    invalid_prefixes = []
    for count in range(1, len(harness_prefix_order) + 1):
        chosen = set(harness_prefix_order[:count])
        violations = sorted(
            (source, target)
            for source, target in effective_edges
            if target in chosen and source not in chosen
        )
        if violations:
            invalid_prefixes.append({"count": count, "violations": violations})

    roots = sorted(active_ids - {target for _, target in effective_edges})
    leaves = sorted(active_ids - {source for source, _ in effective_edges})
    components = weak_components(active_ids, effective_edges)
    sub_named_ids = sorted(mid for mid in active_ids if re.search(r"sub[-_.]?\d+", mid, re.I))

    milestone_images = []
    if image_info:
        for declared_id in image_info["milestone_ids"]:
            milestone_images.append(
                {
                    "milestone_id": declared_id,
                    "hub_image": (
                        f"hyd2apse/{image_info['short_name']}:"
                        f"{declared_id}-v0.9"
                    ),
                    "local_image": (
                        f"{image_info['full_name'].lower()}/"
                        f"{declared_id.lower()}:v0.9"
                    ),
                }
            )

    remote_check: dict[str, Any] = {
        "checked": False,
        "required_tags": [],
        "missing_required_tags": [],
        "base_offline_tag_present": None,
    }
    if image_info and dockerhub_cache_dir:
        cache_path = (
            dockerhub_cache_dir
            / f"swe_milestone_dockerhub_{image_info['short_name']}.json"
        )
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            remote_tags = {
                item.get("name") for item in payload.get("results", []) if item.get("name")
            }
            required_tags = ["base-v0.9", "base-offline-v0.9"] + [
                f"{mid}-v0.9" for mid in image_info["milestone_ids"]
            ]
            remote_check = {
                "checked": True,
                "api_repository": f"hyd2apse/{image_info['short_name']}",
                "api_reported_tag_count": payload.get("count"),
                "required_tags": required_tags,
                "missing_required_tags": sorted(set(required_tags) - remote_tags),
                "base_offline_tag_present": "base-offline-v0.9" in remote_tags,
            }

    return {
        "workspace": repo_dir.name,
        "repo_name": repo_name,
        "catalog_nodes": len(catalog_ids),
        "active_nodes": len(active_ids),
        "graded_nodes": len(graded_ids),
        "non_graded_active_nodes": len(active_ids & nongraded_ids),
        "inactive_catalog_nodes": len(inactive_ids),
        "selected_file_present": selected_path.exists(),
        "base_active_edges": len(base_edges),
        "additional_active_edges": len(additional_edges),
        "effective_active_edges": len(effective_edges),
        "roots": roots,
        "leaves": leaves,
        "weak_component_count": len(components),
        "weak_component_sizes": [len(component) for component in components],
        "active_topological_order": effective_order,
        "active_ids": sorted(active_ids),
        "graded_ids": sorted(graded_ids),
        "non_graded_ids": sorted(active_ids & nongraded_ids),
        "inactive_ids": sorted(inactive_ids),
        "nodes_with_sub_in_id": sub_named_ids,
        "task_contract": {
            "srs_present": len(active_ids) - len(missing_srs),
            "classification_present": len(active_ids) - len(missing_classification),
            "filter_list_present": len(nodes_with_filter),
            "missing_srs": missing_srs,
            "missing_classification": missing_classification,
            "catalog_srs_present": len(catalog_ids) - len(catalog_missing_srs),
            "catalog_classification_present": (
                len(catalog_ids) - len(catalog_missing_classification)
            ),
            "catalog_missing_srs": catalog_missing_srs,
            "catalog_missing_classification": catalog_missing_classification,
            "inactive_with_srs": inactive_with_srs,
            "inactive_with_classification": inactive_with_classification,
        },
        "docker_contract": {
            "manifest_repo_present": image_info is not None,
            "hub_base_image": image_info["hub_base_image"] if image_info else None,
            "local_base_image": image_info["local_base_image"] if image_info else None,
            "hub_base_offline_image": (
                image_info["hub_base_offline_image"] if image_info else None
            ),
            "quarantine_enabled": (
                image_info["quarantine_enabled"] if image_info else None
            ),
            "standard_hub_agent_image": (
                image_info["standard_hub_agent_image"] if image_info else None
            ),
            "standard_local_agent_image": (
                image_info["standard_local_agent_image"] if image_info else None
            ),
            "declared_milestone_images": len(declared_image_ids),
            "missing_active_image_ids": sorted(active_lower - declared_image_ids),
            "extra_image_ids_not_active": sorted(declared_image_ids - active_lower),
            "inactive_catalog_image_ids": sorted(
                {item.lower() for item in inactive_ids} & declared_image_ids
            ),
            "milestone_images": milestone_images,
            "remote_check": remote_check,
        },
        "subdag_contract": {
            "proper_topological_prefixes": max(0, len(active_ids) - 1),
            "harness_prefix_order": harness_prefix_order,
            "invalid_prefixes_under_effective_dag": invalid_prefixes,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SWE-Milestone dataset audit",
        "",
        f"- Dataset commit: `{report['dataset_commit']}`",
        f"- Harness commit: `{report['harness_commit']}`",
        f"- Dataset validator: `{report['dataset_validator']}`",
        f"- Catalog / active / graded nodes: {report['totals']['catalog_nodes']} / "
        f"{report['totals']['active_nodes']} / {report['totals']['graded_nodes']}",
        f"- Active effective edges: {report['totals']['effective_active_edges']}",
        f"- Active milestone task contracts complete: "
        f"{report['totals']['nodes_with_complete_task_contract']} / "
        f"{report['totals']['active_nodes']}",
        f"- Active milestone Docker declarations complete: "
        f"{report['totals']['declared_milestone_images']} / "
        f"{report['totals']['active_nodes']}",
        f"- Inactive catalog nodes with SRS / test classification / Docker declaration: "
        f"{report['totals']['inactive_nodes_with_srs']} / "
        f"{report['totals']['inactive_nodes_with_classification']} / "
        f"{report['totals']['inactive_nodes_with_docker_declaration']} "
        f"(out of {report['totals']['inactive_catalog_nodes']})",
        "",
        "## Per repository",
        "",
        "| Workspace | Catalog | Active | Graded | Edges (base+extra) | SRS/test | Node images | Remote v0.9 | Proper prefix sub-DAGs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for repo in report["repositories"]:
        task = repo["task_contract"]
        docker = repo["docker_contract"]
        remote = docker["remote_check"]
        if remote["checked"]:
            remote_cell = (
                "all present"
                if not remote["missing_required_tags"]
                else f"missing {len(remote['missing_required_tags'])}"
            )
        else:
            remote_cell = "not checked"
        lines.append(
            f"| `{repo['workspace']}` | {repo['catalog_nodes']} | {repo['active_nodes']} | "
            f"{repo['graded_nodes']} | {repo['effective_active_edges']} "
            f"({repo['base_active_edges']}+{repo['additional_active_edges']}) | "
            f"{task['srs_present']}/{task['classification_present']} | "
            f"{docker['declared_milestone_images']} | "
            f"{remote_cell} | "
            f"{repo['subdag_contract']['proper_topological_prefixes']} |"
        )

    lines.extend(
        [
            "",
            "## Whole-DAG agent images",
            "",
            "The standard protected `run_all.py` path finds a quarantine policy for all "
            "seven repositories, so it selects the `base-offline:v0.9` image for the "
            "persistent whole-DAG agent container.",
            "",
            "| Workspace | Docker Hub tag | Local harness tag |",
            "|---|---|---|",
        ]
    )
    for repo in report["repositories"]:
        docker = repo["docker_contract"]
        lines.append(
            f"| `{repo['workspace']}` | `{docker['standard_hub_agent_image']}` | "
            f"`{docker['standard_local_agent_image']}` |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A benchmark task is one repository workspace and its active milestone DAG. "
            "The current protected whole-DAG run uses one repository `base-offline:v0.9` "
            "image; milestone scoring uses the corresponding per-node milestone image.",
            "- Every active milestone is an individually specified task because it has its own "
            "SRS and test-classification file, and the harness exposes `run_milestone`. Every "
            "active milestone also appears in the official Docker pull manifest.",
            "- The 45 inactive catalog rows are not part of the official runnable benchmark "
            "DAG. They may retain partial task artifacts, but the official image manifest does "
            "not assign them milestone images.",
            "- IDs containing `sub-XX` are individual split milestone nodes, not separately "
            "packaged sub-DAGs.",
            "- The harness can dynamically run a topological prefix via `run_e2e --milestones`; "
            "these prefixes reuse the repository base image and included nodes' milestone "
            "images. No prefix has a dedicated Docker image.",
            "- Docker references are declarations from the official harness. When cached "
            "Docker Hub API responses are supplied, the report also verifies the required "
            "`v0.9` tags without pulling image layers.",
            "",
            "## Contract exceptions",
            "",
        ]
    )
    exceptions = []
    for repo in report["repositories"]:
        task = repo["task_contract"]
        docker = repo["docker_contract"]
        remote = docker["remote_check"]
        invalid = repo["subdag_contract"]["invalid_prefixes_under_effective_dag"]
        if task["missing_srs"] or task["missing_classification"]:
            exceptions.append(
                f"- `{repo['workspace']}`: missing task artifacts: "
                f"SRS={task['missing_srs']}, classification={task['missing_classification']}"
            )
        if docker["missing_active_image_ids"] or docker["extra_image_ids_not_active"]:
            exceptions.append(
                f"- `{repo['workspace']}`: image mismatch: "
                f"missing={docker['missing_active_image_ids']}, "
                f"extra={docker['extra_image_ids_not_active']}"
            )
        if remote["checked"] and remote["missing_required_tags"]:
            exceptions.append(
                f"- `{repo['workspace']}`: Docker Hub is missing required tag(s): "
                f"{remote['missing_required_tags']}"
            )
        if invalid:
            first = invalid[0]
            exceptions.append(
                f"- `{repo['workspace']}`: {len(invalid)} `--milestones` prefix(es) are not "
                f"closed under the effective DAG because prefix selection ignores "
                f"`additional_dependencies.csv`; first failure at N={first['count']} with "
                f"edge(s) {first['violations']}."
            )
    lines.extend(exceptions or ["- None."])
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("SWE-Milestone-data"))
    parser.add_argument("--harness", type=Path, default=Path("SWE-Milestone"))
    parser.add_argument("--json-output", type=Path, default=Path("swe_milestone_audit.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("swe_milestone_audit.md"))
    parser.add_argument(
        "--dockerhub-cache-dir",
        type=Path,
        default=None,
        help="Directory containing swe_milestone_dockerhub_<repo>.json API responses",
    )
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    harness = args.harness.resolve()
    image_manifest = parse_image_manifest(harness / "scripts" / "pull_images.sh")
    repo_dirs = sorted(
        path for path in dataset.iterdir() if path.is_dir() and (path / "milestones.csv").is_file()
    )
    repositories = [
        audit_repo(path, image_manifest, args.dockerhub_cache_dir) for path in repo_dirs
    ]

    validator = subprocess.run(
        ["python3", "scripts/validate_data.py"],
        cwd=dataset,
        text=True,
        capture_output=True,
        check=False,
    )
    validator_summary = (validator.stdout + validator.stderr).strip().splitlines()

    totals = {
        "repositories": len(repositories),
        "catalog_nodes": sum(item["catalog_nodes"] for item in repositories),
        "active_nodes": sum(item["active_nodes"] for item in repositories),
        "graded_nodes": sum(item["graded_nodes"] for item in repositories),
        "non_graded_active_nodes": sum(
            item["non_graded_active_nodes"] for item in repositories
        ),
        "inactive_catalog_nodes": sum(
            item["inactive_catalog_nodes"] for item in repositories
        ),
        "inactive_nodes_with_srs": sum(
            len(item["task_contract"]["inactive_with_srs"])
            for item in repositories
        ),
        "inactive_nodes_with_classification": sum(
            len(item["task_contract"]["inactive_with_classification"])
            for item in repositories
        ),
        "inactive_nodes_with_docker_declaration": sum(
            len(item["docker_contract"]["inactive_catalog_image_ids"])
            for item in repositories
        ),
        "effective_active_edges": sum(
            item["effective_active_edges"] for item in repositories
        ),
        "nodes_with_complete_task_contract": sum(
            min(
                item["task_contract"]["srs_present"],
                item["task_contract"]["classification_present"],
            )
            for item in repositories
        ),
        "declared_milestone_images": sum(
            item["docker_contract"]["declared_milestone_images"]
            for item in repositories
        ),
        "proper_topological_prefixes": sum(
            item["subdag_contract"]["proper_topological_prefixes"]
            for item in repositories
        ),
        "invalid_prefixes_under_effective_dag": sum(
            len(item["subdag_contract"]["invalid_prefixes_under_effective_dag"])
            for item in repositories
        ),
        "remote_required_tags_checked": sum(
            len(item["docker_contract"]["remote_check"]["required_tags"])
            for item in repositories
            if item["docker_contract"]["remote_check"]["checked"]
        ),
        "remote_required_tags_missing": sum(
            len(item["docker_contract"]["remote_check"]["missing_required_tags"])
            for item in repositories
            if item["docker_contract"]["remote_check"]["checked"]
        ),
    }
    report = {
        "dataset_path": str(dataset),
        "harness_path": str(harness),
        "dataset_commit": git_head(dataset),
        "harness_commit": git_head(harness),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_validator_exit_code": validator.returncode,
        "dataset_validator": validator_summary[-1] if validator_summary else "no output",
        "totals": totals,
        "repositories": repositories,
    }
    args.json_output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
