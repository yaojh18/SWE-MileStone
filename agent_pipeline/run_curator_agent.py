#!/usr/bin/env python3
"""Run one milestone-curation task through the existing mini SWE agent loop.

This module is intentionally a thin adapter.  Agent control flow, text-action
parsing, LiteLLM calls, trajectories, Singularity execution, and the submission
marker all come from ``RLER/agent/swe_agent``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePath
from typing import Any, Iterator

import yaml

# ``python agent_pipeline/run_curator_agent.py`` is the public Slurm invocation;
# make the workspace package importable without requiring an editable install.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_pipeline.build_views import (
    build_view,
    git,
    metadata_records,
    require_commit,
    sha256_bytes,
)
from agent_pipeline.validate_partition import validate_partition
from agent_pipeline.validate_test_quality import (
    canonical_expanded_decisions_bytes,
    expand_test_quality_output,
    parse_decisions_jsonl,
    validate_test_quality,
)
from agent_pipeline.validation import load_task_view


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "nvidia/zai-org/glm-5.2"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_model_config(path: Path) -> tuple[str, str]:
    """Read the configured token and endpoint without logging either secret value."""
    text = path.read_text(encoding="utf-8")
    token_match = re.search(r"(?mi)^\s*Model API TOKEN:\s*(\S+)\s*$", text)
    endpoint_match = re.search(
        r"(?mi)^\s*inference checkpoint:\s*$\s*^\s*(https?://\S+)\s*$", text
    )
    if not token_match or not endpoint_match:
        raise ValueError(f"could not parse Model API configuration from {path}")
    token = token_match.group(1).strip()
    endpoint = endpoint_match.group(1).strip()
    if not token or not endpoint:
        raise ValueError(f"empty Model API configuration in {path}")
    return token, endpoint


@contextlib.contextmanager
def temporary_environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def load_task_config(task_kind: str) -> dict[str, Any]:
    path = ROOT / "config" / f"{task_kind}.yaml"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("agent"), dict):
        raise ValueError(f"invalid curator config: {path}")
    return payload


def stage_contracts(task_kind: str, task_root: Path) -> None:
    contracts = task_root / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    schema_name = (
        "partition_output.schema.json"
        if task_kind == "partition"
        else "test_quality_output.schema.json"
    )
    shutil.copy2(ROOT / "schemas" / schema_name, contracts / schema_name)


def copy_task_view(task_root: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        task_root,
        destination,
        ignore=shutil.ignore_patterns("output"),
    )


def validate_artifacts(task_kind: str, task_root: Path) -> dict[str, Any]:
    manifest_path = task_root / "output" / "manifest.json"
    if not manifest_path.is_file():
        return {
            "valid": False,
            "errors": ["output/manifest.json: missing"],
            "warnings": [],
            "metrics": {},
        }
    try:
        output = json.loads(manifest_path.read_text(encoding="utf-8"))
        view = load_task_view(task_root)
        if task_kind == "partition":
            report = validate_partition(view, output)
        else:
            decisions_rel = str(output.get("decisions_file", ""))
            decisions_name = PurePath(decisions_rel)
            if (
                decisions_name.is_absolute()
                or len(decisions_name.parts) != 1
                or decisions_name.suffix != ".jsonl"
            ):
                raise ValueError(
                    "decisions_file must be a relative JSONL basename without "
                    "directory traversal"
                )
            decisions_path = task_root / "output" / decisions_rel
            decisions_bytes = decisions_path.read_bytes()
            decisions = parse_decisions_jsonl(decisions_bytes)
            report = validate_test_quality(
                view, output, decisions, decisions_bytes=decisions_bytes
            )
            if report.valid:
                expanded, expansion_report = expand_test_quality_output(
                    view, output, decisions
                )
                if not expansion_report.valid:
                    report.errors.extend(expansion_report.errors)
                else:
                    expanded_bytes = canonical_expanded_decisions_bytes(expanded)
                    expanded_name = "test_decisions.expanded.jsonl"
                    expansion_name = "test_decisions.expansion.json"
                    (task_root / "output" / expanded_name).write_bytes(expanded_bytes)
                    expansion_manifest = {
                        "schema_version": 1,
                        "source": output.get("source"),
                        "source_schema_version": output.get("schema_version"),
                        "source_decisions_file": decisions_rel,
                        "source_decisions_sha256": hashlib.sha256(
                            decisions_bytes
                        ).hexdigest(),
                        "expansion_format": expansion_report.metrics[
                            "expansion_format"
                        ],
                        "expanded_decisions_file": expanded_name,
                        "expanded_decisions_sha256": hashlib.sha256(
                            expanded_bytes
                        ).hexdigest(),
                        "decision_count": len(expanded),
                    }
                    write_json(
                        task_root / "output" / expansion_name,
                        expansion_manifest,
                    )
                    report.metrics.update(
                        {
                            "materialized_expanded_file": expanded_name,
                            "materialized_expansion_manifest": expansion_name,
                            "materialized_expanded_decisions_sha256": hashlib.sha256(
                                expanded_bytes
                            ).hexdigest(),
                        }
                    )
        return report.to_dict()
    except Exception as exc:
        return {
            "valid": False,
            "errors": [f"validator exception: {type(exc).__name__}: {exc}"],
            "warnings": [],
            "metrics": {},
        }


def claim_model_attempt(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"model attempt already recorded at {path}; refusing an automatic second run"
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"started_at={now()} pid={os.getpid()}\n")


def prepare_repository_checkout(
    *,
    dataset: Path,
    workspace: str,
    milestone_id: str,
    repo: Path,
) -> dict[str, Any]:
    """Put a clean curator sandbox at the task's declared START ref.

    Repository-level ``base-offline`` images intentionally serve every
    milestone in one repository, so their default HEAD cannot match every
    task.  This setup happens before the read-only baseline is captured and
    before any model object is constructed.  A dirty image fails closed rather
    than letting ``git checkout`` discard or mingle image-specific state.
    """

    repo_dir = dataset / workspace
    records = {str(item["id"]): item for item in metadata_records(repo_dir)}
    if milestone_id not in records:
        raise KeyError(f"unknown milestone {workspace}/{milestone_id}")
    record = records[milestone_id]
    start_ref = str(record.get("tag_name_start") or record["commit_sha_start"])
    start_commit = require_commit(repo, start_ref)
    initial_head = require_commit(repo, "HEAD")
    initial_status = git(
        repo, "status", "--porcelain=v1", "--untracked-files=all", check=False
    )
    if initial_status:
        raise RuntimeError(
            "curator image repository is dirty before task checkout; refusing "
            f"to discard image state for {workspace}/{milestone_id}: "
            f"{initial_status.splitlines()[:8]}"
        )
    changed = initial_head != start_commit
    if changed:
        git(repo, "checkout", "--detach", start_ref)
    actual_head = require_commit(repo, "HEAD")
    final_status = git(
        repo, "status", "--porcelain=v1", "--untracked-files=all", check=False
    )
    if actual_head != start_commit or final_status:
        raise RuntimeError(
            "curator START checkout preflight failed: "
            f"actual_head={actual_head}, expected={start_commit}, "
            f"status={final_status.splitlines()[:8]}"
        )
    return {
        "workspace": workspace,
        "milestone_id": milestone_id,
        "initial_head": initial_head,
        "start_ref": start_ref,
        "start_commit": start_commit,
        "checkout_changed_head": changed,
        "repository_clean_before": True,
        "repository_clean_after": True,
    }


def run_one(args: argparse.Namespace) -> int:
    # Imports stay here so offline view/schema tests do not need the RLER agent
    # environment installed.  The Slurm wrapper sets PYTHONPATH=/workspace/RLER/agent.
    from swe_agent.agents.default import DefaultAgent
    from swe_agent.config import get_config_from_spec
    from swe_agent.environments import get_environment
    from swe_agent.models import get_model
    from swe_agent.run.run_swe_agent import _litellm_api_base
    from swe_agent.utils.serialize import recursive_merge

    task_kind = args.task_kind.replace("-", "_")
    output_dir = args.output_dir.resolve()
    external_attempt_marker = (
        args.model_attempt_marker.resolve() if args.model_attempt_marker else None
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    run_manifest_path = output_dir / "run_manifest.json"
    if (output_dir / "COMPLETE").exists():
        raise RuntimeError(f"completed curator output already exists: {output_dir}")
    if (output_dir / "MODEL_ATTEMPTED").exists():
        raise RuntimeError(
            f"model attempt already exists in {output_dir}; explicit manual cleanup is required"
        )
    if external_attempt_marker is not None and external_attempt_marker.exists():
        raise RuntimeError(
            f"global model attempt already recorded at {external_attempt_marker}"
        )

    sif = args.sif.resolve()
    dataset = args.dataset.resolve()
    if not sif.is_file() or sif.stat().st_size <= 0:
        raise FileNotFoundError(f"missing or empty SIF: {sif}")
    if not dataset.is_dir():
        raise FileNotFoundError(f"missing dataset: {dataset}")

    token, endpoint = ("", "") if args.preflight_only else parse_model_config(args.model_config)
    pipeline_config = load_task_config(task_kind)
    base_config = get_config_from_spec("mini_textbased")
    config = recursive_merge(base_config, pipeline_config)
    config["agent"] = {
        **config.get("agent", {}),
        "step_limit": args.step_limit,
        "cost_limit": 0,
        "wall_clock_limit_seconds": args.wall_clock_limit_seconds,
        "output_path": output_dir / "trajectory.json",
    }
    config["agent"].pop("mode", None)
    config["environment"] = {
        **config.get("environment", {}),
        "environment_class": "singularity",
        "image": str(sif),
        "cwd": "/testbed",
        "timeout": args.command_timeout,
        "sandbox_build_retries": 3,
    }
    if not args.preflight_only:
        config["model"] = {
            **config.get("model", {}),
            "model_name": args.model,
            "model_class": "litellm_textbased",
            "cost_tracking": "ignore_errors",
            "model_kwargs": {
                **config.get("model", {}).get("model_kwargs", {}),
                "api_base": _litellm_api_base(endpoint),
                "custom_llm_provider": "openai",
                "drop_params": True,
                "temperature": 0,
                "max_tokens": args.max_tokens,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
            },
        }

    env = None
    agent = None
    info: dict[str, Any] = {}
    task_root: Path | None = None
    validation: dict[str, Any] = {
        "valid": False,
        "errors": ["agent did not reach artifact validation"],
        "warnings": [],
        "metrics": {},
    }
    failure: dict[str, Any] | None = None
    baseline_status = ""
    final_status = ""
    checkout_setup: dict[str, Any] | None = None
    started_at = now()
    try:
        # get_environment deep-copies its config; each task receives a distinct
        # writable sandbox and cannot race by mutating shared image/cwd fields.
        env = get_environment(config["environment"], default_type="singularity")
        task_root = Path(env.sandbox_dir) / "task"
        repo = Path(env.sandbox_dir) / "testbed"
        checkout_setup = prepare_repository_checkout(
            dataset=dataset,
            workspace=args.workspace,
            milestone_id=args.milestone_id,
            repo=repo,
        )
        baseline_status = git(
            repo, "status", "--porcelain=v1", "--untracked-files=all", check=False
        )
        input_payload = build_view(
            dataset=dataset,
            workspace=args.workspace,
            milestone_id=args.milestone_id,
            repo=repo,
            output=task_root,
            task_kind=task_kind,
            sif_identity=f"{sif}:{sif.stat().st_size}",
        )
        stage_contracts(task_kind, task_root)
        copy_task_view(task_root, output_dir / "view")
        write_json(output_dir / "input_identity.json", input_payload)

        if args.preflight_only:
            info = {"exit_status": "PreflightComplete", "submission": ""}
            final_status = git(
                repo, "status", "--porcelain=v1", "--untracked-files=all", check=False
            )
            validation = {
                "valid": final_status == baseline_status,
                "errors": (
                    []
                    if final_status == baseline_status
                    else ["repository state changed during curator view preflight"]
                ),
                "warnings": [],
                "metrics": {
                    "input_hash": input_payload["input_hash"],
                    "view_preflight_only": True,
                },
            }
        else:
            # Claim immediately before constructing/querying the model. View,
            # SIF, patch, test, and commit failures therefore consume no model
            # attempt marker.
            claim_model_attempt(output_dir / "MODEL_ATTEMPTED")
            if external_attempt_marker is not None:
                external_attempt_marker.parent.mkdir(parents=True, exist_ok=True)
                claim_model_attempt(external_attempt_marker)
            with temporary_environment(
                {
                    "OPENAI_API_KEY": token,
                    "OPENAI_API_BASE": _litellm_api_base(endpoint),
                    "MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT": "2",
                    "LITELLM_LOG": "ERROR",
                }
            ):
                model = get_model(config=config["model"])
                agent = DefaultAgent(model, env, **config["agent"])
                info = agent.run(
                    f"{args.workspace}/{args.milestone_id} ({task_kind})",
                    workspace=args.workspace,
                    milestone_id=args.milestone_id,
                    input_hash=input_payload["input_hash"],
                )

            final_status = git(
                repo, "status", "--porcelain=v1", "--untracked-files=all", check=False
            )
            if final_status != baseline_status:
                validation = {
                    "valid": False,
                    "errors": [
                        "repository tracked/untracked state changed during read-only curation"
                    ],
                    "warnings": [],
                    "metrics": {},
                }
            elif info.get("exit_status") != "Submitted":
                validation = {
                    "valid": False,
                    "errors": [
                        f"mini SWE agent exit_status={info.get('exit_status')!r}, expected 'Submitted'"
                    ],
                    "warnings": [],
                    "metrics": {},
                }
            else:
                validation = validate_artifacts(task_kind, task_root)
    except Exception as exc:
        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        if agent is not None:
            agent.save(output_dir / "trajectory.json")
    finally:
        if task_root is not None and task_root.exists():
            artifacts = task_root / "output"
            if artifacts.is_dir():
                destination = output_dir / "artifacts"
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(artifacts, destination)
        write_json(output_dir / "validation.json", validation)
        write_json(
            run_manifest_path,
            {
                "schema_version": 1,
                "task_kind": task_kind,
                "workspace": args.workspace,
                "milestone_id": args.milestone_id,
                "model": args.model,
                "model_class": "litellm_textbased",
                "preflight_only": args.preflight_only,
                "mini_swe_agent_reuse": {
                    "agent": "swe_agent.agents.default.DefaultAgent",
                    "environment": "swe_agent.environments.singularity.SingularityEnvironment",
                    "model": "swe_agent.models.litellm_textbased_model.LitellmTextbasedModel",
                    "submission_marker": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                },
                "sif": str(sif),
                "sif_size": sif.stat().st_size if sif.exists() else None,
                "dataset": str(dataset),
                "started_at": started_at,
                "finished_at": now(),
                "exit_status": info.get("exit_status"),
                "submission_sha256": sha256_bytes(
                    str(info.get("submission", "")).encode()
                ),
                "repository_state_unchanged": baseline_status == final_status,
                "repository_checkout_setup": checkout_setup,
                "validation_valid": bool(validation.get("valid")),
                "failure": failure,
            },
        )
        if env is not None and hasattr(env, "cleanup"):
            env.cleanup()

    if failure:
        print(
            f"curator failed: {failure['type']}: {failure['message']}",
            file=sys.stderr,
        )
        return 1
    if not validation.get("valid"):
        print("curator artifact failed deterministic validation", file=sys.stderr)
        return 2
    completion_marker = "PREFLIGHT_COMPLETE" if args.preflight_only else "COMPLETE"
    (output_dir / completion_marker).touch()
    print(
        json.dumps(
            {
                "status": "preflight_complete" if args.preflight_only else "complete",
                "task_kind": task_kind,
                "workspace": args.workspace,
                "milestone_id": args.milestone_id,
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="task_kind", required=True)
    for command in ("partition", "test-quality"):
        child = subparsers.add_parser(command)
        child.add_argument("--dataset", type=Path, required=True)
        child.add_argument("--workspace", required=True)
        child.add_argument("--milestone-id", required=True)
        child.add_argument("--sif", type=Path, required=True)
        child.add_argument("--output-dir", type=Path, required=True)
        child.add_argument("--model-config", type=Path, required=True)
        child.add_argument(
            "--model-attempt-marker",
            type=Path,
            help="Optional cross-run marker claimed only after all view/SIF preflights pass",
        )
        child.add_argument("--model", default=DEFAULT_MODEL)
        child.add_argument("--step-limit", type=int, default=80)
        child.add_argument("--wall-clock-limit-seconds", type=int, default=6900)
        child.add_argument("--command-timeout", type=int, default=300)
        child.add_argument("--max-tokens", type=int, default=16384)
        child.add_argument(
            "--preflight-only",
            action="store_true",
            help="Build and validate the complete SIF task view without constructing a model",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_one(args)


if __name__ == "__main__":
    raise SystemExit(main())
