#!/usr/bin/env python3
"""Run and persist every materialized Dubbo node's own Maven test state.

The runner is invoked once per Slurm rank.  Nodes are deterministically sharded
by manifest index, then tested concurrently on node-local scratch.  Source comes
from ``git archive`` of the clean ref stored in the shared DAG SIF; the SIF keeps
the offline Maven/JDK/runtime closure while the bound worktree supplies exactly
one node tree.  Results use the official harness Surefire XML parser.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
TERMINAL_REUSABLE = {"complete"}
REUSABLE_EVIDENCE_FILES = {
    "test_results": "test_results.json",
    "maven_log": "maven.log",
    "maven_exit_code": "maven.exit_code",
    "surefire_reports": "surefire_reports.tar.gz",
    "test_services": "test-services.json",
}
DEFAULT_TEST_COMMAND = (
    # Match the repository's official base runner parallelism while retaining
    # the full Maven lifecycle: clean node archives deliberately contain no
    # task-derived target/ output, so surefire:test alone is insufficient.
    "mvn -o -B -T 8 -fae test "
    "-Dmaven.repo.local=/opt/swe-milestone-dag-clean/maven-repository "
    "-Dmaven.test.failure.ignore=true "
    "-Dsurefire.timeout=1800 "
    "-Dsurefire.forkCount=4 "
    "-Dsurefire.reuseForks=false "
    "-Dsurefire.parallel=none "
    "-DenableEmbeddedZookeeper=false "
    # Disabling Dubbo's per-fork listener also suppresses the side effect that
    # normally publishes these addresses.  Point every fork at the two common
    # services explicitly so test semantics stay identical without allowing a
    # test fork to start or stop the DAG-wide processes.
    "-Dzookeeper.connection.address=zookeeper://127.0.0.1:2181 "
    "-Dzookeeper.connection.address.1=zookeeper://127.0.0.1:2181 "
    "-Dzookeeper.connection.address.2=zookeeper://127.0.0.1:2182 "
    "-DembeddedZookeeperPath=/testbed/.tmp/zookeeper "
    "-DtrimStackTrace=false "
    "-Pjacoco,jdk15ge-simple,!jdk15ge-add-open,skip-spotless "
    "-Dcheckstyle.skip=true -Drat.skip=true"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def runtime_reuse_identity(
    sif: Path,
    runtime_fingerprint_file: Path | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Return the runtime portion of a node result's reuse identity.

    Historically the runner bound every result to the byte digest of the
    complete SIF.  That remains the exact behavior when no fingerprint file is
    supplied.  A post-hoist SIF also contains all clean Git tags, however, so a
    change to an unrelated node would otherwise invalidate every completed
    endpoint.  In v2 mode the caller supplies a stable file whose *contents*
    identify only the common runtime/environment closure.  The node's own
    ``clean_sha`` and ``clean_tree`` remain separate mandatory identity fields.

    The file is deliberately treated as an opaque, immutable declaration.  Its
    path is provenance only and is not part of the reusable identity, allowing
    the same declaration to move between resumed run directories.  Exact-byte
    hashing is conservative: even an edited declaration must be reviewed as a
    new runtime identity.
    """

    if runtime_fingerprint_file is None:
        digest = sha256_file(sif)
        return (
            {"sif_sha256": digest},
            {
                "mode": "legacy_full_sif_sha256",
                "sif": str(sif),
                "sif_sha256": digest,
            },
        )

    if not runtime_fingerprint_file.is_file():
        raise RuntimeError(f"runtime fingerprint file is missing: {runtime_fingerprint_file}")
    size = runtime_fingerprint_file.stat().st_size
    if size <= 0:
        raise RuntimeError(f"runtime fingerprint file is empty: {runtime_fingerprint_file}")
    digest = sha256_file(runtime_fingerprint_file)
    return (
        {"runtime_fingerprint_sha256": digest},
        {
            "mode": "declared_common_runtime_environment",
            "fingerprint_file": str(runtime_fingerprint_file),
            "fingerprint_file_bytes": size,
            "runtime_fingerprint_sha256": digest,
            # Record the work image path for audit, but intentionally do not
            # hash or bind it in this mode.  Node source identity is verified
            # independently against clean_sha/clean_tree below.
            "sif": str(sif),
        },
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_capture(command: Sequence[str], *, timeout: int | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )


def safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive, "r:") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise RuntimeError(f"archive member escapes destination: {member.name!r}") from exc
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                try:
                    link_target.relative_to(destination_resolved)
                except ValueError as exc:
                    raise RuntimeError(f"archive link escapes destination: {member.name!r}") from exc
        handle.extractall(destination)


def verify_clean_ref(
    apptainer: str,
    sif: Path,
    ref: str,
    expected_sha: str,
    expected_tree: str,
    env: dict[str, str],
) -> None:
    """Prove that the tag archived from this SIF is the manifest node.

    This check is essential when the whole SIF digest is no longer part of the
    reuse identity: a stale or incorrectly assembled image must not be allowed
    to serve a same-named tag with different source content.
    """

    if not re.fullmatch(r"[A-Za-z0-9._/-]+", ref):
        raise RuntimeError(f"unsafe clean ref: {ref!r}")
    resolved: list[str] = []
    for object_type in ("commit", "tree"):
        proc = run_capture(
            [
                apptainer,
                "exec",
                str(sif),
                "git",
                "-C",
                "/testbed",
                "rev-parse",
                "--verify",
                f"{ref}^{{{object_type}}}",
            ],
            env=env,
        )
        if proc.returncode:
            detail = proc.stderr.decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"cannot resolve clean ref {ref!r} {object_type}: {detail}")
        resolved.append(proc.stdout.decode("utf-8", errors="replace").strip())
    actual_sha, actual_tree = resolved
    if actual_sha != expected_sha or actual_tree != expected_tree:
        raise RuntimeError(
            "clean ref identity mismatch: "
            f"{ref} resolved to {actual_sha}/{actual_tree}, "
            f"manifest requires {expected_sha}/{expected_tree}"
        )


def archive_ref(
    apptainer: str,
    sif: Path,
    ref: str,
    archive_path: Path,
    env: dict[str, str],
    *,
    expected_sha: str,
    expected_tree: str,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", ref):
        raise RuntimeError(f"unsafe clean ref: {ref!r}")
    verify_clean_ref(apptainer, sif, ref, expected_sha, expected_tree, env)
    command = [
        apptainer,
        "exec",
        str(sif),
        "/bin/bash",
        "-lc",
        f"cd /testbed && git archive --format=tar {ref}",
    ]
    with archive_path.open("wb") as output:
        proc = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, env=env)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[-4000:])


def copy_runtime_support(
    apptainer: str,
    sif: Path,
    worktree: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    # .tmp/zookeeper is an untracked runtime asset baked into the base image and
    # is hidden when the clean node tree is bound over /testbed.
    support_archive = worktree.parent / "runtime-support.tar"
    command = [
        apptainer,
        "exec",
        str(sif),
        "/bin/bash",
        "-lc",
        "cd /testbed && if test -d .tmp/zookeeper; then "
        "tar -cf - .tmp/zookeeper; else tar -cf - --files-from /dev/null; fi",
    ]
    with support_archive.open("wb") as output:
        proc = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, env=env)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[-4000:])
    safe_extract(support_archive, worktree)
    return {
        "source": "/testbed/.tmp/zookeeper",
        "archive_sha256": sha256_file(support_archive),
        "archive_bytes": support_archive.stat().st_size,
    }


def parser_identity(harness_root: Path) -> dict[str, Any]:
    parser_path = harness_root / "harness" / "utils" / "maven_surefire_xml_utils.py"
    commit = run_capture(["git", "-C", str(harness_root), "rev-parse", "HEAD"])
    parser_relative = parser_path.relative_to(harness_root)
    dirty = run_capture(
        ["git", "-C", str(harness_root), "status", "--porcelain", "--", str(parser_relative)]
    )
    return {
        "path": str(parser_path),
        "sha256": sha256_file(parser_path),
        "harness_commit": commit.stdout.decode().strip() if commit.returncode == 0 else None,
        "parser_dirty": bool(dirty.stdout.strip()),
    }


def load_official_parser(harness_root: Path):
    sys.path.insert(0, str(harness_root))
    from harness.utils.maven_surefire_xml_utils import collect_all_surefire_reports

    return collect_all_surefire_reports


def existing_reusable(result_path: Path, identity: dict[str, Any]) -> bool:
    if not result_path.is_file():
        return False
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("status") not in TERMINAL_REUSABLE or payload.get("identity") != identity:
        return False
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return False
    for name, filename in REUSABLE_EVIDENCE_FILES.items():
        record = evidence.get(name)
        path = result_path.parent / filename
        if (
            not isinstance(record, dict)
            or record.get("filename") != filename
            or not path.is_file()
            or record.get("sha256") != sha256_file(path)
        ):
            return False
    return True


def evidence_manifest(output_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "filename": filename,
            "sha256": sha256_file(output_dir / filename) if (output_dir / filename).is_file() else None,
            "exists": (output_dir / filename).is_file(),
        }
        for name, filename in REUSABLE_EVIDENCE_FILES.items()
    }


def load_test_scope_review(review_dir: Path) -> dict[str, Any]:
    request_path = review_dir / "request.json"
    decision_path = review_dir / "decision.json"
    if not request_path.is_file() or not decision_path.is_file():
        raise RuntimeError(f"test-scope review is incomplete: {review_dir}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    subject_sha256 = canonical_sha256(request.get("subject"))
    if request.get("review_subject_sha256") != subject_sha256:
        raise RuntimeError("test-scope request fingerprint does not match its subject")
    if decision.get("review_subject_sha256") != subject_sha256:
        raise RuntimeError("test-scope decision does not approve the current request subject")
    if decision.get("decision") != "approve_dag_wide_executable_test_module_scope":
        raise RuntimeError("test-scope decision is not an approval")
    request_sha256 = sha256_file(request_path)
    decision_sha256 = sha256_file(decision_path)
    return {
        "review_id": request.get("review_id"),
        "request": str(request_path),
        "request_sha256": request_sha256,
        "decision": str(decision_path),
        "decision_sha256": decision_sha256,
        "review_subject_sha256": subject_sha256,
        "review_bundle_sha256": sha256_text(f"{request_sha256}\n{decision_sha256}\n"),
    }


def discover_test_modules(worktree: Path) -> list[str]:
    """Return Maven modules with checked-in or explicitly imported tests.

    The policy is deliberately repository-wide and content-derived: it never
    consumes milestone IDs, statements, or declared test lists.  Local
    ``src/test`` ownership covers ordinary tests.  Maven declarations cover
    modules that import tests from a test-jar (Surefire ``dependenciesToScan``)
    or use nonstandard/Failsafe test sources.  ``-am`` later adds every product
    dependency needed by this executable scope, while unrelated demo
    applications with no tests do not become an implicit full-reactor oracle.
    """

    root = worktree.resolve()
    modules: set[str] = set()
    for candidate in worktree.rglob("*"):
        if not candidate.is_file():
            continue
        relative_parts = candidate.relative_to(worktree).parts
        if not any(
            relative_parts[index : index + 2] == ("src", "test")
            for index in range(len(relative_parts) - 1)
        ):
            continue
        directory = candidate.parent.resolve()
        while True:
            if (directory / "pom.xml").is_file():
                relative = directory.relative_to(root)
                modules.add("." if str(relative) == "." else relative.as_posix())
                break
            if directory == root:
                raise RuntimeError(f"test file has no owning Maven module: {candidate}")
            directory = directory.parent
    configured_markers = (
        "<dependenciesToScan",
        "<testSourceDirectory",
        "<goal>add-test-source</goal>",
        "<artifactId>maven-failsafe-plugin</artifactId>",
        "<goal>integration-test</goal>",
    )
    for pom in worktree.rglob("pom.xml"):
        if not pom.is_file():
            continue
        content = pom.read_text(encoding="utf-8", errors="replace")
        if any(marker in content for marker in configured_markers):
            relative = pom.parent.resolve().relative_to(root)
            modules.add("." if str(relative) == "." else relative.as_posix())
    if not modules:
        raise RuntimeError("clean node contains no Maven modules with executable test configuration")
    return sorted(modules)


def scoped_test_command(test_command: str, modules: Sequence[str]) -> str:
    if not modules:
        raise ValueError("test module scope must not be empty")
    selector = ",".join(modules)
    return f"{test_command} -pl {shlex.quote(selector)} -am"


def build_test_script(test_command: str) -> str:
    return f"""
set -uo pipefail
cd /testbed
find . -type d -name target -prune -exec rm -rf -- {{}} +
service_root=/dag-output/test-services-runtime
service_ctl=/opt/swe-milestone-dag-clean/run_test_services.sh
cleanup_services() {{
    "$service_ctl" stop "$service_root" || true
    cp -f "$service_root"/zookeeper-*.log /dag-output/ 2>/dev/null || true
}}
trap cleanup_services EXIT
started=$(date -Is)
set +e
"$service_ctl" start "$service_root" > /dag-output/test-services.log 2>&1
service_code=$?
if [[ "$service_code" -eq 0 ]]; then
    cp -f "$service_root/manifest.json" /dag-output/test-services.json
    {test_command} > /dag-output/maven.log 2>&1
    code=$?
    "$service_ctl" probe "$service_root" > /dag-output/test-services-probe.log 2>&1
    probe_code=$?
    cp -f "$service_root/manifest.json" /dag-output/test-services.json
else
    printf 'test runtime service setup failed with exit code %s\n' "$service_code" > /dag-output/maven.log
    code=90
fi
set -e
printf '%s\n' "$code" > /dag-output/maven.exit_code
printf '%s\n' "$started" > /dag-output/maven.started_at
date -Is > /dag-output/maven.completed_at
rm -rf /tmp/dag-clean-surefire
mkdir -p /tmp/dag-clean-surefire
find /testbed -path '*/target/surefire-reports' -type d | while read -r directory; do
    module=${{directory#/testbed/}}
    module=${{module%/target/surefire-reports}}
    mkdir -p "/tmp/dag-clean-surefire/$module"
    cp -f "$directory"/TEST-*.xml "/tmp/dag-clean-surefire/$module/" 2>/dev/null || true
done
tar -C /tmp -czf /dag-output/surefire_reports.tar.gz dag-clean-surefire
exit "$code"
"""


def test_one(
    node: dict[str, Any],
    *,
    sif: Path,
    runtime_identity_fields: dict[str, str],
    output_root: Path,
    scratch_root: Path,
    apptainer: str,
    test_command: str,
    timeout: int,
    collect_reports: Any,
    parser_info: dict[str, Any],
    scope_review: dict[str, Any],
    runner_sha256: str,
    rank: int,
) -> dict[str, Any]:
    artifact_name = node["node_id"].replace(":", "__")
    output_dir = output_root / "nodes" / artifact_name / "test"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    effective_test_command = test_command
    test_scope: dict[str, Any] = {
        "policy": "maven_modules_with_local_or_explicitly_imported_tests",
        "modules": None,
        "modules_sha256": None,
    }
    identity = {
        "node_id": node["node_id"],
        "clean_sha": node["clean_sha"],
        "clean_tree": node["clean_tree"],
        "test_command_sha256": None,
        "test_scope_sha256": None,
        "parser_sha256": parser_info["sha256"],
        "test_scope_review_sha256": scope_review["review_bundle_sha256"],
        "runner_sha256": runner_sha256,
        **runtime_identity_fields,
    }

    node_scratch = scratch_root / artifact_name
    if node_scratch.exists():
        shutil.rmtree(node_scratch)
    worktree = node_scratch / "testbed"
    worktree.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "APPTAINER_CACHEDIR": str(node_scratch / "apptainer-cache"),
            "APPTAINER_TMPDIR": str(node_scratch / "apptainer-tmp"),
            "SINGULARITY_CACHEDIR": str(node_scratch / "apptainer-cache"),
            "SINGULARITY_TMPDIR": str(node_scratch / "apptainer-tmp"),
        }
    )
    Path(env["APPTAINER_CACHEDIR"]).mkdir(parents=True)
    Path(env["APPTAINER_TMPDIR"]).mkdir(parents=True)
    started_at = utc_now()
    monotonic_start = time.monotonic()
    status = "error"
    error: str | None = None
    return_code: int | None = None
    parsed: dict[str, Any] = {"tests": [], "summary": {"total": 0}}
    runtime_support: dict[str, Any] | None = None
    test_services: dict[str, Any] = {"status": "not_started"}
    reuse_existing = False
    try:
        archive = node_scratch / "node.tar"
        archive_ref(
            apptainer,
            sif,
            node["clean_tag"],
            archive,
            env,
            expected_sha=node["clean_sha"],
            expected_tree=node["clean_tree"],
        )
        safe_extract(archive, worktree)
        modules = discover_test_modules(worktree)
        modules_digest = sha256_text("\n".join(modules) + "\n")
        effective_test_command = scoped_test_command(test_command, modules)
        test_scope.update({"modules": modules, "modules_sha256": modules_digest})
        identity.update(
            {
                "test_command_sha256": sha256_text(effective_test_command),
                "test_scope_sha256": modules_digest,
            }
        )
        reuse_existing = existing_reusable(result_path, identity)
        if not reuse_existing:
            runtime_support = copy_runtime_support(apptainer, sif, worktree, env)
            command = [
                apptainer,
                "exec",
                "--writable-tmpfs",
                "--bind",
                f"{worktree}:/testbed",
                "--bind",
                f"{output_dir}:/dag-output",
                str(sif),
                "/bin/bash",
                "-lc",
                build_test_script(effective_test_command),
            ]
            try:
                proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
                return_code = proc.returncode
                (output_dir / "apptainer.stdout").write_bytes(proc.stdout)
                (output_dir / "apptainer.stderr").write_bytes(proc.stderr)
            except subprocess.TimeoutExpired as exc:
                status = "timeout"
                error = f"test command exceeded {timeout} seconds"
                (output_dir / "apptainer.stdout").write_bytes(exc.stdout or b"")
                (output_dir / "apptainer.stderr").write_bytes(exc.stderr or b"")

            parsed = collect_reports(worktree).to_dict()
            write_json(output_dir / "test_results.json", parsed)
            services_path = output_dir / "test-services.json"
            if services_path.is_file():
                try:
                    test_services = json.loads(services_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    test_services = {"status": "invalid_manifest"}
            else:
                test_services = {"status": "missing"}
            log_text = (output_dir / "maven.log").read_text(encoding="utf-8", errors="replace") if (output_dir / "maven.log").is_file() else ""
            build_success = "BUILD SUCCESS" in log_text
            build_failure = "BUILD FAILURE" in log_text
            services_ready = test_services.get("status") == "ready"
            if status != "timeout":
                if build_success and parsed.get("summary", {}).get("total", 0) > 0 and services_ready:
                    status = "complete"
                elif not services_ready:
                    status = "runtime_service_failure"
                elif build_failure:
                    status = "build_failure"
                elif parsed.get("summary", {}).get("total", 0) == 0:
                    status = "no_test_reports"
                else:
                    status = "incomplete"
    except Exception as exc:  # evidence must land even for infrastructure failures
        error = f"{type(exc).__name__}: {exc}"
        status = "error"
    finally:
        duration = round(time.monotonic() - monotonic_start, 3)
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "identity": identity,
            "node_id": node["node_id"],
            "milestone_id": node["milestone_id"],
            "role": node["role"],
            "clean_tag": node["clean_tag"],
            "rank": rank,
            "started_at": started_at,
            "completed_at": utc_now(),
            "duration_seconds": duration,
            "return_code": return_code,
            "test_command": effective_test_command,
            "test_scope": test_scope,
            "test_scope_review": scope_review,
            "execution_policy": {
                "maven_offline": " -o " in f" {effective_test_command} " or " --offline " in f" {effective_test_command} ",
                "fresh_source_archive": True,
                "writable_tmpfs": True,
                "external_network_isolated": False,
            },
            "runtime_support": runtime_support,
            "test_services": test_services,
            "test_summary": parsed.get("summary", {}),
            "evidence": evidence_manifest(output_dir),
            "parser": parser_info,
            "error": error,
        }
        if not reuse_existing:
            write_json(result_path, result)
        if node_scratch.exists():
            shutil.rmtree(node_scratch)
    if reuse_existing:
        return {"node_id": node["node_id"], "status": "existing_complete", "result": str(result_path)}
    return {"node_id": node["node_id"], "status": status, "result": str(result_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dag-manifest", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--test-scope-review-dir", type=Path, required=True)
    parser.add_argument(
        "--runtime-fingerprint-file",
        type=Path,
        help=(
            "stable, nonempty file identifying only the common runtime/environment; "
            "when omitted, preserve legacy reuse identity by hashing the complete SIF"
        ),
    )
    parser.add_argument("--rank", type=int, default=int(os.environ.get("SLURM_PROCID", "0")))
    parser.add_argument("--world", type=int, default=int(os.environ.get("SLURM_NTASKS", "1")))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--test-command", default=DEFAULT_TEST_COMMAND)
    parser.add_argument("--only-node", action="append", default=[])
    args = parser.parse_args()

    if args.rank < 0 or args.world <= 0 or args.rank >= args.world:
        raise SystemExit(f"invalid rank/world: {args.rank}/{args.world}")
    if args.workers <= 0 or args.timeout <= 0:
        raise SystemExit("workers and timeout must be positive")
    for required in (
        args.dag_manifest,
        args.sif,
        args.harness_root / "harness",
        args.test_scope_review_dir,
        *([args.runtime_fingerprint_file] if args.runtime_fingerprint_file is not None else []),
    ):
        if not required.exists():
            raise SystemExit(f"missing input: {required}")
    if shutil.which("apptainer") is None:
        raise SystemExit("apptainer is not available")

    dag = json.loads(args.dag_manifest.read_text(encoding="utf-8"))
    nodes = sorted(dag["nodes"], key=lambda item: int(item["index"]))
    if args.only_node:
        requested = set(args.only_node)
        nodes = [node for node in nodes if node["node_id"] in requested]
        missing = requested - {node["node_id"] for node in nodes}
        if missing:
            raise SystemExit(f"unknown --only-node values: {sorted(missing)}")
    else:
        nodes = [node for node in nodes if int(node["index"]) % args.world == args.rank]

    args.output_root.mkdir(parents=True, exist_ok=True)
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    runtime_identity_fields, runtime_info = runtime_reuse_identity(
        args.sif,
        args.runtime_fingerprint_file,
    )
    parser_info = parser_identity(args.harness_root)
    scope_review = load_test_scope_review(args.test_scope_review_dir)
    runner_sha256 = sha256_file(Path(__file__).resolve())
    collect_reports = load_official_parser(args.harness_root)
    runner_manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "rank": args.rank,
        "world": args.world,
        "workers": args.workers,
        "selected_nodes": [node["node_id"] for node in nodes],
        "sif": str(args.sif),
        "runtime_identity": runtime_info,
        "test_command": args.test_command,
        "execution_policy": {
            "maven_offline": " -o " in f" {args.test_command} " or " --offline " in f" {args.test_command} ",
            "workers_share_host_network": args.workers > 1,
        },
        "parser": parser_info,
        "test_scope_review": scope_review,
        "runner_sha256": runner_sha256,
        "started_at": utc_now(),
        **(
            {"sif_sha256": runtime_info["sif_sha256"]}
            if runtime_info["mode"] == "legacy_full_sif_sha256"
            else {}
        ),
    }
    write_json(args.output_root / f"test_runner.rank{args.rank}.json", runner_manifest)

    outcomes: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                test_one,
                node,
                sif=args.sif,
                runtime_identity_fields=runtime_identity_fields,
                output_root=args.output_root,
                scratch_root=args.scratch_root,
                apptainer=shutil.which("apptainer") or "apptainer",
                test_command=args.test_command,
                timeout=args.timeout,
                collect_reports=collect_reports,
                parser_info=parser_info,
                scope_review=scope_review,
                runner_sha256=runner_sha256,
                rank=args.rank,
            )
            for node in nodes
        ]
        for future in concurrent.futures.as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            print(json.dumps(outcome, sort_keys=True), flush=True)

    counts = Counter(outcome["status"] for outcome in outcomes)
    complete = all(outcome["status"] in {"complete", "existing_complete"} for outcome in outcomes)
    runner_manifest.update(
        {
            "status": "complete" if complete else "completed_with_failures",
            "completed_at": utc_now(),
            "counts": dict(counts),
            "outcomes": sorted(outcomes, key=lambda item: item["node_id"]),
        }
    )
    write_json(args.output_root / f"test_runner.rank{args.rank}.json", runner_manifest)
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
