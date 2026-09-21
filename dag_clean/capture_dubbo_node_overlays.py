#!/usr/bin/env python3
"""Capture effective Dubbo START/END trees and their post-hoist overlays.

The base repository's ``milestone-*-start/end`` tags are the post-hoist
authority.  For a milestone with a downloaded SIF, the effective endpoint is
captured by starting a fresh writable overlay, checking out the endpoint tag,
running ``/usr/local/bin/apply_patches.sh`` when present, and writing the Git
index tree.  The capture is repeated from another fresh writable overlay.

If a milestone SIF is not available, the milestone Dockerfile's RUN, WORKDIR,
and ENV instructions are replayed in one isolated writable instance of the
base SIF.  Pure Maven build/install/test-compile invocations are omitted, while
Spotless invocations are intentionally retained because they mutate sources.
The replay is also performed twice from fresh repository clones.

Container captures export only a Git tree archive.  This program imports that
archive into the base repository, computes a deletion-aware binary patch from
the post-hoist tag, and proves that applying the patch reconstructs the exact
effective tree.  Per-endpoint manifests and the aggregate manifest are written
atomically and are content-addressed for safe resume.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
EXPECTED_DUBBO_MILESTONES = 26
ENDPOINT_ROLES = ("start", "end")
SAFE_REF = re.compile(r"[A-Za-z0-9._/-]+\Z")


class CaptureError(RuntimeError):
    """Raised when an endpoint cannot be captured without guessing."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode:
        stdout = proc.stdout.decode("utf-8", errors="replace")[-2000:]
        stderr = proc.stderr.decode("utf-8", errors="replace")[-6000:]
        raise CaptureError(
            f"command failed ({proc.returncode}): {list(command)!r}\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return proc


def git(repo: Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return run(["git", "-C", str(repo), *args], **kwargs)


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode("utf-8", errors="strict").strip()


def safe_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    if not result or result in {".", ".."}:
        raise CaptureError(f"unsafe path component: {value!r}")
    return result


@dataclass(frozen=True)
class EndpointSpec:
    milestone_id: str
    role: str
    tag: str
    declared_sha: str

    @property
    def node_id(self) -> str:
        return f"{self.milestone_id}:{self.role}"

    @property
    def artifact_name(self) -> str:
        return f"{safe_component(self.milestone_id)}__{self.role}"


@dataclass(frozen=True)
class DockerInstruction:
    kind: str
    value: str
    line: int


@dataclass(frozen=True)
class CaptureSource:
    kind: str
    path: Path
    digest: str
    replay_script: str | None = None
    manifest_record: dict[str, Any] | None = None

    def identity(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "path": str(self.path),
            "sha256": self.digest,
        }
        if self.replay_script is not None:
            payload["replay_script_sha256"] = sha256_bytes(
                self.replay_script.encode("utf-8")
            )
        if self.manifest_record is not None:
            payload["sif_manifest_record"] = self.manifest_record
        return payload


@dataclass(frozen=True)
class RawCapture:
    archive: Path
    details: dict[str, Any]


def load_endpoints(metadata_path: Path) -> list[EndpointSpec]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    milestones = metadata.get("milestones")
    if not isinstance(milestones, list):
        raise CaptureError("metadata.milestones must be a list")
    if len(milestones) != EXPECTED_DUBBO_MILESTONES:
        raise CaptureError(
            f"expected {EXPECTED_DUBBO_MILESTONES} Dubbo milestones, found {len(milestones)}"
        )

    seen_ids: set[str] = set()
    seen_nodes: set[str] = set()
    endpoints: list[EndpointSpec] = []
    for row in milestones:
        milestone_id = str(row.get("id", "")).strip()
        if not milestone_id or milestone_id in seen_ids:
            raise CaptureError(f"missing or duplicate milestone id: {milestone_id!r}")
        seen_ids.add(milestone_id)
        for role in ENDPOINT_ROLES:
            tag = str(row.get(f"tag_name_{role}", "")).strip()
            declared = str(row.get(f"commit_sha_{role}", "")).strip()
            if not tag or not SAFE_REF.fullmatch(tag):
                raise CaptureError(f"invalid {role} tag for {milestone_id}: {tag!r}")
            endpoint = EndpointSpec(milestone_id, role, tag, declared)
            if endpoint.node_id in seen_nodes:
                raise CaptureError(f"duplicate endpoint: {endpoint.node_id}")
            seen_nodes.add(endpoint.node_id)
            endpoints.append(endpoint)
    return endpoints


def resolve_post_hoist(repo: Path, endpoint: EndpointSpec) -> tuple[str, str]:
    proc = git(repo, "rev-parse", f"{endpoint.tag}^{{commit}}", check=False)
    if proc.returncode:
        raise CaptureError(
            f"post-hoist tag is missing for {endpoint.node_id}: {endpoint.tag}\n"
            + proc.stderr.decode("utf-8", errors="replace")
        )
    sha = proc.stdout.decode().strip()
    tree = git_text(repo, "rev-parse", f"{sha}^{{tree}}")
    return sha, tree


def sif_candidates(root: Path, milestone_id: str) -> list[Path]:
    names = [
        f"{milestone_id}.sif",
        f"{milestone_id.lower()}.sif",
        f"{safe_component(milestone_id).lower()}.sif",
    ]
    result: list[Path] = []
    for name in names:
        candidate = root / name
        if candidate not in result:
            result.append(candidate)
    return result


def find_sif(root: Path, milestone_id: str) -> Path | None:
    matches = [path for path in sif_candidates(root, milestone_id) if path.is_file()]
    if len(matches) > 1:
        identities = {path.resolve() for path in matches}
        if len(identities) > 1:
            raise CaptureError(
                f"ambiguous milestone SIF for {milestone_id}: {[str(path) for path in matches]}"
            )
    return matches[0] if matches else None


def load_sif_manifest(
    path: Path,
    *,
    workspace: str,
    sif_root: Path,
) -> dict[str, tuple[Path, dict[str, Any]]]:
    """Resolve active images from manifest provenance, never from filenames alone."""

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureError(f"invalid SIF manifest JSON at line {line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise CaptureError(f"SIF manifest line {line_number} is not an object")
        if str(record.get("workspace", "")) == workspace:
            records.append(record)

    result: dict[str, tuple[Path, dict[str, Any]]] = {}
    for record in records:
        milestone_id = str(record.get("milestone_id", "")).strip()
        destination_rel = str(record.get("destination_rel", "")).strip()
        if not milestone_id or not destination_rel:
            raise CaptureError(f"incomplete SIF manifest record: {record!r}")
        key = milestone_id.casefold()
        if key in result:
            raise CaptureError(
                f"case-insensitive SIF manifest collision for {workspace}/{milestone_id}"
            )
        relative = Path(destination_rel)
        if relative.is_absolute() or ".." in relative.parts:
            raise CaptureError(f"unsafe SIF destination_rel: {destination_rel!r}")
        if relative.parts and relative.parts[0] == workspace:
            relative = Path(*relative.parts[1:])
        image = sif_root / relative
        result[key] = (image, record)
    return result


def _continuation_open(value: str) -> bool:
    return bool(re.search(r"\\\s*\Z", value))


def _heredoc_delimiters(value: str) -> list[str]:
    return [
        match.group(1)
        for match in re.finditer(r"<<-?\s*(?:['\"])?([A-Za-z_][A-Za-z0-9_]*)", value)
    ]


def parse_dockerfile(text: str) -> list[DockerInstruction]:
    """Parse the instruction subset needed for deterministic source replay.

    The parser preserves RUN shell text, including Dockerfile continuations and
    heredocs.  It intentionally does not attempt to implement image-layer
    instructions such as FROM, COPY, USER, or ENTRYPOINT.
    """

    lines = text.splitlines()
    instructions: list[DockerInstruction] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        match = re.match(r"^\s*([A-Za-z]+)\s+(.*)$", raw)
        if not match or raw.lstrip().startswith("#"):
            index += 1
            continue
        kind = match.group(1).upper()
        start_line = index + 1
        pieces = [match.group(2)]
        index += 1

        while _continuation_open(pieces[-1]) and index < len(lines):
            pieces.append(lines[index])
            index += 1

        value = "\n".join(pieces)
        pending = _heredoc_delimiters(value)
        for delimiter in pending:
            found = False
            while index < len(lines):
                line = lines[index]
                pieces.append(line)
                index += 1
                if line.strip() == delimiter:
                    found = True
                    break
            if not found:
                raise CaptureError(
                    f"unterminated Dockerfile heredoc {delimiter!r} at line {start_line}"
                )
        instructions.append(DockerInstruction(kind, "\n".join(pieces), start_line))
    return instructions


def is_pure_maven_build(command: str) -> bool:
    """Return true for a standalone Maven build command that can be skipped."""

    lowered = command.lower()
    if re.search(r"(?:^|\s)(?:[^\s]*:)?spotless:|spotless:apply|spotless:check", lowered):
        return False
    # Remove a leading `cd ... &&` chain, which is how these Dockerfiles invoke
    # Maven.  Commands that also mutate sources before Maven are retained.
    candidate = command.strip().replace("\\\n", " ")
    candidate = re.sub(r"^(?:cd\s+[^;&|]+\s*&&\s*)+", "", candidate)
    candidate = candidate.lstrip()
    return bool(re.match(r"^(?:mvn|mvnw|\./mvnw)\s+", candidate))


def _env_exports(value: str) -> list[str]:
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise CaptureError(f"invalid ENV instruction: {value!r}: {exc}") from exc
    if not tokens:
        raise CaptureError("empty ENV instruction")
    if "=" not in tokens[0]:
        if len(tokens) < 2:
            raise CaptureError(f"legacy ENV instruction has no value: {value!r}")
        return [f"export {tokens[0]}={shlex.quote(' '.join(tokens[1:]))}"]
    exports: list[str] = []
    for token in tokens:
        if "=" not in token:
            raise CaptureError(f"malformed ENV assignment: {token!r}")
        key, assigned = token.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise CaptureError(f"unsafe ENV key: {key!r}")
        # Preserve shell expansion of values such as ${MAVEN_HOME} and ${PATH}.
        escaped = assigned.replace("\\", "\\\\").replace('"', '\\"')
        exports.append(f'export {key}="{escaped}"')
    return exports


def build_docker_replay_script(instructions: Sequence[DockerInstruction]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -Ee -o pipefail",
        "DOCKER_WORKDIR=/testbed",
        "mkdir -p \"$DOCKER_WORKDIR\"",
    ]
    retained_runs = 0
    for instruction in instructions:
        if instruction.kind == "WORKDIR":
            value = instruction.value.strip()
            if not value:
                raise CaptureError(f"empty WORKDIR at line {instruction.line}")
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.extend(
                [
                    f"# Dockerfile line {instruction.line}: WORKDIR",
                    f'DOCKER_WORKDIR="{escaped}"',
                    'mkdir -p "$DOCKER_WORKDIR"',
                ]
            )
        elif instruction.kind == "ENV":
            lines.append(f"# Dockerfile line {instruction.line}: ENV")
            lines.extend(_env_exports(instruction.value))
        elif instruction.kind == "RUN":
            if is_pure_maven_build(instruction.value):
                lines.append(
                    f"# Dockerfile line {instruction.line}: skipped pure Maven build"
                )
                continue
            retained_runs += 1
            lines.extend(
                [
                    f"# Dockerfile line {instruction.line}: RUN",
                    "(",
                    '  cd "$DOCKER_WORKDIR"',
                    instruction.value,
                    ")",
                ]
            )
    if retained_runs == 0:
        raise CaptureError("Dockerfile replay contains no retained RUN instructions")
    return "\n".join(lines) + "\n"


CAPTURE_ONE_SCRIPT = r'''#!/usr/bin/env bash
set -Eeuo pipefail
tag=${1:?tag}
archive=${2:?archive}
details=${3:?details}
cd /testbed
git config user.name "SWE Milestone Overlay Capture"
git config user.email "swe-milestone-capture@example.invalid"
git checkout -f "$tag" >&2
git reset --hard "$tag" >&2
git clean -ffd >&2
apply_present=false
apply_sha256=null
if [[ -f /usr/local/bin/apply_patches.sh ]]; then
  apply_present=true
  apply_sha256=$(sha256sum /usr/local/bin/apply_patches.sh | awk '{print $1}')
  bash /usr/local/bin/apply_patches.sh >&2
fi
git add -A
tree=$(git write-tree)
head=$(git rev-parse HEAD)
head_tree=$(git rev-parse HEAD^{tree})
tmp_archive="${archive}.tmp.$$"
git archive --format=tar "$tree" > "$tmp_archive"
mv -f "$tmp_archive" "$archive"
tmp_details="${details}.tmp.$$"
printf '{"apply_patches_present":%s,"apply_patches_sha256":%s,"checked_out_sha":"%s","checked_out_tree":"%s","effective_tree_in_source_repo":"%s"}\n' \
  "$apply_present" \
  "$([[ "$apply_sha256" == null ]] && printf null || printf '"%s"' "$apply_sha256")" \
  "$head" "$head_tree" "$tree" > "$tmp_details"
mv -f "$tmp_details" "$details"
'''


def _apptainer_command(
    apptainer: str,
    image: Path,
    capture_dir: Path,
    script_name: str,
    arguments: Sequence[str],
    *,
    extra_binds: Sequence[str] = (),
) -> list[str]:
    command = [
        apptainer,
        "exec",
        "--writable-tmpfs",
        "--bind",
        f"{capture_dir}:/capture",
    ]
    for bind in extra_binds:
        command.extend(("--bind", bind))
    command.extend(
        (
            str(image),
            "/bin/bash",
            f"/capture/{script_name}",
            *arguments,
        )
    )
    return command


def capture_from_sif(
    endpoint: EndpointSpec,
    attempt_dir: Path,
    *,
    sif: Path,
    apptainer: str,
) -> RawCapture:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    script = attempt_dir / "capture-one.sh"
    atomic_write_bytes(script, CAPTURE_ONE_SCRIPT.encode("utf-8"))
    script.chmod(0o755)
    archive = attempt_dir / "tree.tar"
    details_path = attempt_dir / "capture.json"
    proc = run(
        _apptainer_command(
            apptainer,
            sif,
            attempt_dir,
            script.name,
            (endpoint.tag, "/capture/tree.tar", "/capture/capture.json"),
        )
    )
    atomic_write_bytes(attempt_dir / "apptainer.stdout", proc.stdout)
    atomic_write_bytes(attempt_dir / "apptainer.stderr", proc.stderr)
    if not archive.is_file() or not details_path.is_file():
        raise CaptureError(f"SIF capture did not produce artifacts for {endpoint.node_id}")
    details = json.loads(details_path.read_text(encoding="utf-8"))
    details.update(
        {
            "capture_backend": "milestone_sif",
            "sif": str(sif),
            "archive_sha256": sha256_file(archive),
        }
    )
    return RawCapture(archive, details)


def _clone_base_repo(base_repo: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    proc = run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            "--local",
            str(base_repo),
            str(destination),
        ],
        check=False,
    )
    if proc.returncode:
        # `--local` cannot cross every scratch filesystem.  The ordinary clone
        # is slower but preserves the same refs and object identities.
        if destination.exists():
            shutil.rmtree(destination)
        run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                str(base_repo),
                str(destination),
            ]
        )
    # A Dockerfile starts from the base image's checked-out /testbed, not from
    # an empty --no-checkout clone.  Several Dubbo Dockerfiles save files before
    # their first explicit milestone checkout, so materialize the cloned HEAD.
    git(destination, "checkout", "-f", "HEAD")
    git(destination, "reset", "--hard", "HEAD")


def build_replay_and_capture_script(
    replay_script: str,
    endpoints: Sequence[EndpointSpec],
) -> str:
    lines = [replay_script.rstrip(), "", "# Export effective endpoint trees."]
    lines.append("capture_endpoint() {")
    lines.extend(
        [
            "  local tag=$1 role=$2",
            "  cd /testbed",
            "  git checkout -f \"$tag\" >&2",
            "  git reset --hard \"$tag\" >&2",
            "  git clean -ffd >&2",
            "  local apply_present=false apply_sha256=null",
            "  if [[ -f /usr/local/bin/apply_patches.sh ]]; then",
            "    apply_present=true",
            "    apply_sha256=$(sha256sum /usr/local/bin/apply_patches.sh | awk '{print $1}')",
            "    bash /usr/local/bin/apply_patches.sh >&2",
            "  fi",
            "  git add -A",
            "  local tree head head_tree",
            "  tree=$(git write-tree)",
            "  head=$(git rev-parse HEAD)",
            "  head_tree=$(git rev-parse HEAD^{tree})",
            "  git archive --format=tar \"$tree\" > \"/capture/${role}.tar.tmp\"",
            "  mv -f \"/capture/${role}.tar.tmp\" \"/capture/${role}.tar\"",
            "  printf '{\"apply_patches_present\":%s,\"apply_patches_sha256\":%s,\"checked_out_sha\":\"%s\",\"checked_out_tree\":\"%s\",\"effective_tree_in_source_repo\":\"%s\"}\\n' \\",
            "    \"$apply_present\" \\",
            "    \"$([[ \"$apply_sha256\" == null ]] && printf null || printf '\"%s\"' \"$apply_sha256\")\" \\",
            "    \"$head\" \"$head_tree\" \"$tree\" > \"/capture/${role}.json.tmp\"",
            "  mv -f \"/capture/${role}.json.tmp\" \"/capture/${role}.json\"",
            "}",
        ]
    )
    for endpoint in endpoints:
        lines.append(
            f"capture_endpoint {shlex.quote(endpoint.tag)} {shlex.quote(endpoint.role)}"
        )
    return "\n".join(lines) + "\n"


def capture_milestone_from_dockerfile(
    endpoints: Sequence[EndpointSpec],
    attempt_dir: Path,
    *,
    base_repo: Path,
    base_sif: Path,
    dockerfile: Path,
    apptainer: str,
) -> dict[str, RawCapture]:
    if {endpoint.role for endpoint in endpoints} != set(ENDPOINT_ROLES):
        raise CaptureError("Dockerfile replay requires both START and END endpoints")
    instructions = parse_dockerfile(dockerfile.read_text(encoding="utf-8"))
    replay = build_docker_replay_script(instructions)
    combined = build_replay_and_capture_script(replay, endpoints)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    replay_path = attempt_dir / "replay-and-capture.sh"
    atomic_write_bytes(replay_path, combined.encode("utf-8"))
    replay_path.chmod(0o755)

    replay_repo = attempt_dir / "testbed"
    _clone_base_repo(base_repo, replay_repo)
    git(replay_repo, "config", "user.name", "SWE Milestone Docker Replay")
    git(replay_repo, "config", "user.email", "swe-milestone-replay@example.invalid")
    proc = run(
        _apptainer_command(
            apptainer,
            base_sif,
            attempt_dir,
            replay_path.name,
            (),
            extra_binds=(f"{replay_repo}:/testbed",),
        )
    )
    atomic_write_bytes(attempt_dir / "apptainer.stdout", proc.stdout)
    atomic_write_bytes(attempt_dir / "apptainer.stderr", proc.stderr)

    captured: dict[str, RawCapture] = {}
    for endpoint in endpoints:
        archive = attempt_dir / f"{endpoint.role}.tar"
        details_path = attempt_dir / f"{endpoint.role}.json"
        if not archive.is_file() or not details_path.is_file():
            raise CaptureError(
                f"Dockerfile replay did not capture {endpoint.node_id}: {attempt_dir}"
            )
        details = json.loads(details_path.read_text(encoding="utf-8"))
        details.update(
            {
                "capture_backend": "dockerfile_replay",
                "dockerfile": str(dockerfile),
                "dockerfile_sha256": sha256_file(dockerfile),
                "replay_script_sha256": sha256_bytes(replay.encode("utf-8")),
                "archive_sha256": sha256_file(archive),
            }
        )
        captured[endpoint.role] = RawCapture(archive, details)
    return captured


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, "r:") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise CaptureError(f"archive path escapes tree: {member.name!r}") from exc
            if member.issym() or member.islnk():
                link = (target.parent / member.linkname).resolve()
                try:
                    link.relative_to(root)
                except ValueError as exc:
                    raise CaptureError(
                        f"archive link escapes tree: {member.name!r} -> {member.linkname!r}"
                    ) from exc
        handle.extractall(destination)


def _clear_worktree(worktree: Path) -> None:
    for child in worktree.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def import_archive_tree(
    repo: Path,
    base_sha: str,
    archive: Path,
    worktree: Path,
) -> str:
    if worktree.exists():
        shutil.rmtree(worktree)
    git(repo, "worktree", "add", "--detach", str(worktree), base_sha)
    try:
        _clear_worktree(worktree)
        _safe_extract_tar(archive, worktree)
        git(worktree, "add", "-A")
        return git_text(worktree, "write-tree")
    finally:
        git(repo, "worktree", "remove", "--force", str(worktree), check=False)
        if worktree.exists():
            shutil.rmtree(worktree)


def changed_paths(repo: Path, start: str, end: str) -> list[str]:
    raw = git(
        repo,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        start,
        end,
    ).stdout
    paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    ]
    if len(paths) != len(set(paths)):
        raise CaptureError(f"duplicate paths in overlay {start}..{end}")
    return sorted(paths)


def binary_patch(repo: Path, start: str, end: str) -> bytes:
    return git(
        repo,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-renames",
        start,
        end,
    ).stdout


def verify_patch_reconstruction(
    repo: Path,
    start_sha: str,
    expected_tree: str,
    patch: bytes,
    worktree: Path,
) -> dict[str, Any]:
    if worktree.exists():
        shutil.rmtree(worktree)
    git(repo, "worktree", "add", "--detach", str(worktree), start_sha)
    try:
        if patch:
            proc = git(
                worktree,
                "apply",
                "--index",
                "--binary",
                input_bytes=patch,
                check=False,
            )
            if proc.returncode:
                return {
                    "ok": False,
                    "apply_ok": False,
                    "actual_tree": None,
                    "expected_tree": expected_tree,
                    "git_error": proc.stderr.decode("utf-8", errors="replace"),
                }
        actual = git_text(worktree, "write-tree")
        return {
            "ok": actual == expected_tree,
            "apply_ok": True,
            "actual_tree": actual,
            "expected_tree": expected_tree,
        }
    finally:
        git(repo, "worktree", "remove", "--force", str(worktree), check=False)
        if worktree.exists():
            shutil.rmtree(worktree)


def patch_stats(patch: bytes) -> dict[str, int]:
    additions = deletions = files = 0
    for line in patch.decode("utf-8", errors="replace").splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {
        "files": files,
        "additions": additions,
        "deletions": deletions,
        "loc": additions + deletions,
    }


def endpoint_fingerprint(
    endpoint: EndpointSpec,
    post_hoist_sha: str,
    post_hoist_tree: str,
    source: CaptureSource,
    *,
    base_sif_digest: str | None,
    program_digest: str,
) -> str:
    return canonical_sha256(
        {
            "schema_version": SCHEMA_VERSION,
            "node_id": endpoint.node_id,
            "tag": endpoint.tag,
            "declared_sha": endpoint.declared_sha,
            "post_hoist_sha": post_hoist_sha,
            "post_hoist_tree": post_hoist_tree,
            "capture_source": source.identity(),
            "base_sif_sha256": base_sif_digest,
            "program_sha256": program_digest,
            "capture_repetitions": 2,
        }
    )


def reusable_manifest(
    manifest_path: Path,
    expected_fingerprint: str,
    output_root: Path,
) -> dict[str, Any] | None:
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        patch_record = manifest["effective.patch"]
        patch_path = manifest_path.parent / patch_record["path"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if not (
        manifest.get("status") == "complete"
        and manifest.get("input_fingerprint") == expected_fingerprint
        and manifest.get("repeat_capture_match") is True
        and manifest.get("patch_reconstruction", {}).get("ok") is True
        and patch_path.is_file()
        and sha256_file(patch_path) == patch_record.get("sha256")
    ):
        return None
    return manifest


def materialize_endpoint(
    endpoint: EndpointSpec,
    *,
    base_repo: Path,
    post_hoist_sha: str,
    post_hoist_tree: str,
    captures: Sequence[RawCapture],
    source: CaptureSource,
    input_fingerprint: str,
    output_root: Path,
    scratch: Path,
) -> dict[str, Any]:
    if len(captures) != 2:
        raise CaptureError(f"exactly two captures are required for {endpoint.node_id}")
    trees: list[str] = []
    for index, capture in enumerate(captures):
        trees.append(
            import_archive_tree(
                base_repo,
                post_hoist_sha,
                capture.archive,
                scratch / f"import-{endpoint.artifact_name}-{index}",
            )
        )
    repeat_match = trees[0] == trees[1]
    if not repeat_match:
        raise CaptureError(
            f"fresh captures disagree for {endpoint.node_id}: {trees[0]} != {trees[1]}"
        )
    effective_tree = trees[0]
    patch = binary_patch(base_repo, post_hoist_sha, effective_tree)
    paths = changed_paths(base_repo, post_hoist_sha, effective_tree)
    reconstruction = verify_patch_reconstruction(
        base_repo,
        post_hoist_sha,
        effective_tree,
        patch,
        scratch / f"reconstruct-{endpoint.artifact_name}",
    )
    if not reconstruction["ok"]:
        raise CaptureError(
            f"overlay patch does not reconstruct {endpoint.node_id}: {reconstruction}"
        )

    endpoint_dir = output_root / endpoint.artifact_name
    patch_path = endpoint_dir / "effective.patch"
    atomic_write_bytes(patch_path, patch)
    output_relative_patch = patch_path.relative_to(output_root).as_posix()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "node_id": endpoint.node_id,
        "milestone_id": endpoint.milestone_id,
        "role": endpoint.role,
        "tag": endpoint.tag,
        "declared_sha_audit_only": endpoint.declared_sha,
        "post_hoist_sha": post_hoist_sha,
        "post_hoist_tree": post_hoist_tree,
        "effective_tree": effective_tree,
        "effective.patch": {
            "path": "effective.patch",
            "output_relative_path": output_relative_patch,
            "sha256": sha256_bytes(patch),
            "bytes": len(patch),
            "stats": patch_stats(patch),
        },
        "capture_source": source.identity(),
        "capture_attempts": [capture.details for capture in captures],
        "repeat_capture_match": repeat_match,
        "repeat_effective_trees": trees,
        "overlay_changed_paths": paths,
        "patch_reconstruction": reconstruction,
        "input_fingerprint": input_fingerprint,
        "completed_at": utc_now(),
    }
    atomic_write_json(endpoint_dir / "manifest.json", manifest)
    return manifest


class CaptureCoordinator:
    """Cache paired Dockerfile captures so one replay serves both endpoints."""

    def __init__(
        self,
        *,
        base_repo: Path,
        base_sif: Path | None,
        sif_root: Path,
        sif_records: dict[str, tuple[Path, dict[str, Any]]],
        docker_root: Path,
        scratch: Path,
        apptainer: str,
        capture_mode: str,
    ) -> None:
        self.base_repo = base_repo
        self.base_sif = base_sif
        self.sif_root = sif_root
        self.sif_records = sif_records
        self.docker_root = docker_root
        self.scratch = scratch
        self.apptainer = apptainer
        self.capture_mode = capture_mode
        self._docker_captures: dict[tuple[str, int], dict[str, RawCapture]] = {}
        self._digest_cache: dict[Path, str] = {}

    def digest(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved not in self._digest_cache:
            self._digest_cache[resolved] = sha256_file(path)
        return self._digest_cache[resolved]

    def source_for(self, milestone_id: str) -> CaptureSource:
        manifest_entry = self.sif_records.get(milestone_id.casefold())
        if self.capture_mode in {"auto", "sif"} and manifest_entry is not None:
            sif, record = manifest_entry
            if not sif.is_file():
                raise CaptureError(
                    f"active milestone image declared by manifest is missing for "
                    f"{milestone_id}: {sif}"
                )
            return CaptureSource(
                "milestone_sif",
                sif,
                self.digest(sif),
                manifest_record=record,
            )
        if self.capture_mode == "sif":
            raise CaptureError(
                f"capture mode sif but no manifest record exists for {milestone_id}"
            )
        dockerfile = self.docker_root / milestone_id / "Dockerfile"
        if not dockerfile.is_file():
            raise CaptureError(f"Dockerfile is missing for inactive milestone {milestone_id}")
        if self.base_sif is None or not self.base_sif.is_file():
            raise CaptureError(
                f"base SIF is required for Dockerfile replay of {milestone_id}"
            )
        replay = build_docker_replay_script(
            parse_dockerfile(dockerfile.read_text(encoding="utf-8"))
        )
        return CaptureSource(
            "dockerfile_replay",
            dockerfile,
            self.digest(dockerfile),
            replay,
        )

    def capture(
        self,
        endpoint: EndpointSpec,
        attempt: int,
        paired_endpoints: Sequence[EndpointSpec],
        source: CaptureSource,
    ) -> RawCapture:
        if source.kind == "milestone_sif":
            return capture_from_sif(
                endpoint,
                self.scratch / endpoint.artifact_name / f"attempt-{attempt}",
                sif=source.path,
                apptainer=self.apptainer,
            )
        key = (endpoint.milestone_id, attempt)
        if key not in self._docker_captures:
            assert self.base_sif is not None
            self._docker_captures[key] = capture_milestone_from_dockerfile(
                paired_endpoints,
                self.scratch
                / safe_component(endpoint.milestone_id)
                / f"docker-attempt-{attempt}",
                base_repo=self.base_repo,
                base_sif=self.base_sif,
                dockerfile=source.path,
                apptainer=self.apptainer,
            )
        return self._docker_captures[key][endpoint.role]


@contextlib.contextmanager
def endpoint_lock(endpoint_dir: Path) -> Iterable[None]:
    """Serialize accidental duplicate workers without leaving a stale lock."""

    endpoint_dir.mkdir(parents=True, exist_ok=True)
    lock_path = endpoint_dir / ".capture.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def shard_endpoints(
    endpoints: Sequence[EndpointSpec],
    *,
    rank: int,
    world: int,
    shard_by: str,
) -> list[EndpointSpec]:
    if rank < 0 or world <= 0 or rank >= world:
        raise CaptureError(f"invalid rank/world: {rank}/{world}")
    if shard_by == "endpoint":
        return [endpoint for index, endpoint in enumerate(endpoints) if index % world == rank]
    if shard_by != "milestone":
        raise CaptureError(f"invalid shard mode: {shard_by}")
    milestone_order = list(dict.fromkeys(endpoint.milestone_id for endpoint in endpoints))
    owned = {
        milestone
        for index, milestone in enumerate(milestone_order)
        if index % world == rank
    }
    return [endpoint for endpoint in endpoints if endpoint.milestone_id in owned]


def maybe_write_aggregate_manifest(
    output: Path,
    *,
    world: int,
    run_fingerprint: str,
    expected_endpoint_count: int,
) -> dict[str, Any] | None:
    summaries: list[dict[str, Any]] = []
    for rank in range(world):
        path = output / f"summary.rank{rank}.json"
        if not path.is_file():
            return None
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            summary.get("rank") != rank
            or summary.get("world") != world
            or summary.get("run_fingerprint") != run_fingerprint
        ):
            return None
        summaries.append(summary)

    endpoint_paths = sorted(
        path
        for summary in summaries
        for path in summary.get("endpoint_manifests", [])
    )
    errors = [error for summary in summaries for error in summary.get("errors", [])]
    complete = (
        not errors
        and all(summary.get("status") == "complete" for summary in summaries)
        and len(endpoint_paths) == expected_endpoint_count
        and len(set(endpoint_paths)) == expected_endpoint_count
    )
    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete" if complete else "completed_with_errors",
        "completed_at": utc_now(),
        "world": world,
        "run_fingerprint": run_fingerprint,
        "expected_endpoint_count": expected_endpoint_count,
        "captured_endpoint_count": len(endpoint_paths),
        "resumed_endpoint_count": sum(
            int(summary.get("resumed_endpoint_count", 0)) for summary in summaries
        ),
        "endpoint_manifests": endpoint_paths,
        "rank_summaries": [f"summary.rank{rank}.json" for rank in range(world)],
        "errors": errors,
    }
    atomic_write_json(output / "manifest.json", aggregate)
    return aggregate


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--base-repo", type=Path, required=True)
    parser.add_argument("--docker-root", type=Path, required=True)
    parser.add_argument("--sif-root", type=Path, required=True)
    parser.add_argument("--sif-manifest", type=Path, required=True)
    parser.add_argument("--base-sif", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--capture-mode", choices=("auto", "sif", "dockerfile"), default="auto")
    parser.add_argument("--apptainer", default="apptainer")
    parser.add_argument("--only-milestone", action="append", default=[])
    parser.add_argument("--rank", type=int, default=int(os.environ.get("SLURM_PROCID", "0")))
    parser.add_argument("--world", type=int, default=int(os.environ.get("SLURM_NTASKS", "1")))
    parser.add_argument("--shard-by", choices=("milestone", "endpoint"), default="milestone")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    for required in (
        args.metadata,
        args.base_repo / ".git",
        args.docker_root,
        args.sif_manifest,
    ):
        if not required.exists():
            raise CaptureError(f"missing required input: {required}")
    if shutil.which(args.apptainer) is None:
        raise CaptureError(f"apptainer executable is unavailable: {args.apptainer}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    all_endpoints = load_endpoints(args.metadata)
    requested = set(args.only_milestone)
    known = {endpoint.milestone_id for endpoint in all_endpoints}
    if requested - known:
        raise CaptureError(f"unknown --only-milestone values: {sorted(requested - known)}")
    selected_endpoints = [
        endpoint
        for endpoint in all_endpoints
        if not requested or endpoint.milestone_id in requested
    ]
    endpoints = shard_endpoints(
        selected_endpoints,
        rank=args.rank,
        world=args.world,
        shard_by=args.shard_by,
    )
    by_milestone: dict[str, list[EndpointSpec]] = {}
    for endpoint in selected_endpoints:
        by_milestone.setdefault(endpoint.milestone_id, []).append(endpoint)

    program_digest = sha256_file(Path(__file__).resolve())
    base_sif_digest = sha256_file(args.base_sif) if args.base_sif and args.base_sif.is_file() else None
    workspace = args.metadata.parent.name
    sif_records = load_sif_manifest(
        args.sif_manifest,
        workspace=workspace,
        sif_root=args.sif_root,
    )
    unknown_manifest_ids = sorted(
        set(sif_records) - {endpoint.milestone_id.casefold() for endpoint in all_endpoints}
    )
    if unknown_manifest_ids:
        raise CaptureError(
            f"SIF manifest declares unknown milestones for {workspace}: {unknown_manifest_ids}"
        )
    run_subject = {
        "schema_version": SCHEMA_VERSION,
        "metadata_sha256": sha256_file(args.metadata),
        "sif_manifest_sha256": sha256_file(args.sif_manifest),
        "base_sif_sha256": base_sif_digest,
        "program_sha256": program_digest,
        "capture_mode": args.capture_mode,
        "requested_milestones": sorted(requested),
        "world": args.world,
        "shard_by": args.shard_by,
    }
    run_fingerprint = canonical_sha256(run_subject)
    coordinator = CaptureCoordinator(
        base_repo=args.base_repo,
        base_sif=args.base_sif,
        sif_root=args.sif_root,
        sif_records=sif_records,
        docker_root=args.docker_root,
        scratch=args.scratch / f"rank-{args.rank}",
        apptainer=args.apptainer,
        capture_mode=args.capture_mode,
    )
    rank_summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "started_at": utc_now(),
        "rank": args.rank,
        "world": args.world,
        "shard_by": args.shard_by,
        "run_fingerprint": run_fingerprint,
        "run_subject": run_subject,
        "metadata": str(args.metadata),
        "sif_manifest": str(args.sif_manifest),
        "base_repo": str(args.base_repo),
        "base_sif": str(args.base_sif) if args.base_sif else None,
        "base_sif_sha256": base_sif_digest,
        "capture_mode": args.capture_mode,
        "program_sha256": program_digest,
        "global_expected_endpoint_count": len(selected_endpoints),
        "rank_expected_endpoint_count": len(endpoints),
        "selected_nodes": [endpoint.node_id for endpoint in endpoints],
        "endpoint_manifests": [],
        "errors": [],
    }
    summary_path = args.output / f"summary.rank{args.rank}.json"
    atomic_write_json(summary_path, rank_summary)

    manifests: list[dict[str, Any]] = []
    resumed = 0
    for endpoint in endpoints:
        endpoint_dir = args.output / endpoint.artifact_name
        manifest_path = endpoint_dir / "manifest.json"
        with endpoint_lock(endpoint_dir):
            try:
                post_sha, post_tree = resolve_post_hoist(args.base_repo, endpoint)
                source = coordinator.source_for(endpoint.milestone_id)
                fingerprint = endpoint_fingerprint(
                    endpoint,
                    post_sha,
                    post_tree,
                    source,
                    base_sif_digest=base_sif_digest if source.kind == "dockerfile_replay" else None,
                    program_digest=program_digest,
                )
                reusable = reusable_manifest(manifest_path, fingerprint, args.output)
                if reusable is not None:
                    manifests.append(reusable)
                    resumed += 1
                    continue
                captures = [
                    coordinator.capture(
                        endpoint,
                        attempt,
                        by_milestone[endpoint.milestone_id],
                        source,
                    )
                    for attempt in (1, 2)
                ]
                manifest = materialize_endpoint(
                    endpoint,
                    base_repo=args.base_repo,
                    post_hoist_sha=post_sha,
                    post_hoist_tree=post_tree,
                    captures=captures,
                    source=source,
                    input_fingerprint=fingerprint,
                    output_root=args.output,
                    scratch=coordinator.scratch,
                )
                manifests.append(manifest)
            except Exception as exc:
                error = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "error",
                    "node_id": endpoint.node_id,
                    "milestone_id": endpoint.milestone_id,
                    "role": endpoint.role,
                    "error": f"{type(exc).__name__}: {exc}",
                    "failed_at": utc_now(),
                }
                atomic_write_json(manifest_path, error)
                rank_summary["errors"].append(error)

    rank_summary.update(
        {
            "status": "complete" if not rank_summary["errors"] else "completed_with_errors",
            "completed_at": utc_now(),
            "captured_endpoint_count": len(manifests),
            "resumed_endpoint_count": resumed,
            "endpoint_manifests": [
                str((args.output / manifest["node_id"].replace(":", "__") / "manifest.json").relative_to(args.output))
                for manifest in manifests
            ],
        }
    )
    atomic_write_json(summary_path, rank_summary)
    aggregate = maybe_write_aggregate_manifest(
        args.output,
        world=args.world,
        run_fingerprint=run_fingerprint,
        expected_endpoint_count=len(selected_endpoints),
    )
    print(json.dumps({"rank_summary": rank_summary, "aggregate": aggregate}, sort_keys=True))
    return 0 if rank_summary["status"] == "complete" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CaptureError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(2)
