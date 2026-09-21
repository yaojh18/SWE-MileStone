#!/usr/bin/env python3

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


class RunDubboUnifiedCleanSlurmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (Path(__file__).parent / "run_dubbo_unified_clean.slurm").read_text(
            encoding="utf-8"
        )

    def test_owns_exactly_two_nodes_and_two_tasks(self) -> None:
        self.assertRegex(self.script, r"(?m)^#SBATCH --nodes=2$")
        self.assertRegex(self.script, r"(?m)^#SBATCH --ntasks=2$")
        self.assertRegex(self.script, r"(?m)^#SBATCH --ntasks-per-node=1$")

    def test_full_step_is_failure_isolated_and_resumable(self) -> None:
        self.assertIn("--kill-on-bad-exit=0", self.script)
        self.assertIn("--defer-failure-exit", self.script)
        self.assertIn("--precomputed-inputs", self.script)
        self.assertIn("scontrol requeue", self.script)

    def test_uses_exact_source_closure_and_no_online_hydration(self) -> None:
        self.assertIn("source_sif_closure.json", self.script)
        self.assertNotIn("hydrate_dubbo_runtime.sh", self.script)
        self.assertNotRegex(self.script, r"SIF_ROOT[^\n]*\*")

    def test_explicit_prior_closure_checkpoint_is_validated_before_reuse(self) -> None:
        self.assertIn("DUBBO_UNIFIED_CLOSURE_SELECTION_SOURCE", self.script)
        self.assertIn('payload["adopted_from"]', self.script)
        self.assertIn("explicit closure selection checkpoint failed validation", self.script)
        adoption = self.script.index('payload["adopted_from"]')
        validation = self.script.index('d.get("source_closure_sha256") == sha(source_closure)')
        extraction = self.script.index('python3 "$CODE_ROOT/extract_sif_maven_repositories.py"')
        self.assertLess(adoption, validation)
        self.assertLess(validation, extraction)

    def test_final_gate_keeps_milestone_and_gap_denominators_distinct(self) -> None:
        self.assertIn('structural.get("transition_count")!=33', self.script)
        self.assertIn('structural.get("gap_count")!=8', self.script)
        self.assertIn('"structural_transitions":33', self.script)

    def test_runtime_gate_verifies_installed_closure_and_single_anchor(self) -> None:
        self.assertIn("verify_maven_runtime_closure.py", self.script)
        self.assertIn('git rev-list --all --count', self.script)
        self.assertIn("--cleanenv --no-home", self.script)
        self.assertIn('FINAL_VERIFY_SANDBOX="$LOCAL_ROOT/final-verify-sandbox"', self.script)
        self.assertIn(
            'apptainer build --sandbox "$FINAL_VERIFY_SANDBOX" "$LOCAL_SIF"',
            self.script,
        )
        self.assertNotIn(
            '"$LOCAL_SIF" python3 /opt/swe-milestone-unified/verify_maven_runtime_closure.py',
            self.script,
        )

    def test_agent_sif_excludes_controller_provenance_and_cwd(self) -> None:
        self.assertNotIn(
            'install -m 0444 "$SNAPSHOT/clean/source_sif_closure.json"',
            self.script,
        )
        self.assertNotIn(
            'install -m 0444 "$RUNTIME_DIR/agent_anchor.json"', self.script
        )
        self.assertIn("--no-mount cwd --pwd /testbed", self.script)
        self.assertIn("--contain --no-mount cwd", self.script)

    def test_runtime_environment_is_executed_and_fingerprinted(self) -> None:
        self.assertIn("99-swe-milestone-unified.sh", self.script)
        self.assertIn("unified_dubbo_environment.sh", self.script)
        self.assertIn('fingerprint_command+=(--input', self.script)
        self.assertIn("--execution-engine-identity", self.script)
        self.assertIn("execution_engine_identity_sha256", self.script)

    def test_outer_engine_is_compared_on_both_ranks_before_reuse(self) -> None:
        engine = self.script.split(
            "write_terminal running execution_engine", 1
        )[1].split("write_terminal running maven_closure", 1)[0]
        self.assertIn("--nodes=2 --ntasks=2", engine)
        self.assertIn("probe_outer_container_runtime.py", engine)
        self.assertIn("ranks[0]!=ranks[1]", engine)
        self.assertIn('status":"epoch_drift"', engine)

    def test_requeue_and_publication_are_identity_safe(self) -> None:
        self.assertIn('rm -rf -- "$LOCAL_ROOT"', self.script)
        self.assertIn('cmp -s "$LOCAL_SIF" "$destination_tmp"', self.script)
        self.assertIn("sif_publication.json", self.script)
        self.assertIn("unexpected launcher exit code", self.script)

    def test_snapshot_and_rank_terminal_are_closed(self) -> None:
        self.assertIn('files = sorted(path for path in root.rglob("*") if path.is_file())', self.script)
        self.assertIn('set(declared)!=actual', self.script)
        self.assertIn('runner_status in {"complete","completed_with_failures"}', self.script)

    def test_shell_and_every_embedded_program_parse(self) -> None:
        script_path = Path(__file__).parent / "run_dubbo_unified_clean.slurm"
        subprocess.run(["bash", "-n", str(script_path)], check=True)
        lines = self.script.splitlines()
        python_blocks = []
        outer_blocks = []
        index = 0
        while index < len(lines):
            line = lines[index]
            marker = None
            destination = None
            if "<<'PY'" in line:
                marker, destination = "PY", python_blocks
            elif "<<'OUTER'" in line:
                marker, destination = "OUTER", outer_blocks
            if marker is not None:
                end = index + 1
                while end < len(lines) and lines[end] != marker:
                    end += 1
                self.assertLess(end, len(lines), f"unterminated {marker} heredoc")
                destination.append("\n".join(lines[index + 1 : end]) + "\n")
            index += 1
        self.assertGreaterEqual(len(python_blocks), 10)
        self.assertEqual(len(outer_blocks), 3)
        for number, source in enumerate(python_blocks):
            compile(source, f"launcher-heredoc-{number}", "exec")
        for source in outer_blocks:
            subprocess.run(["bash", "-n"], input=source, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
