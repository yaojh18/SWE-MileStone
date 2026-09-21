#!/usr/bin/env python3

from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_dubbo_posthoist_repair.slurm")


class RepairLauncherContractTest(unittest.TestCase):
    def test_embedded_heredocs_parse(self) -> None:
        lines = SCRIPT.read_text(encoding="utf-8").splitlines()
        index = 0
        counts = {"PY": 0, "OUTER": 0}
        while index < len(lines):
            match = re.search(r"<<'(?P<kind>PY|OUTER)'", lines[index])
            if match is None:
                index += 1
                continue
            kind = match.group("kind")
            start = index + 1
            index = start
            while index < len(lines) and lines[index] != kind:
                index += 1
            self.assertLess(index, len(lines), f"unterminated {kind} heredoc at line {start}")
            source = "\n".join(lines[start:index]) + "\n"
            if kind == "PY":
                ast.parse(source, filename=f"{SCRIPT}:{start}")
            else:
                result = subprocess.run(
                    ["bash", "-n"], input=source, text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0, f"OUTER heredoc line {start}: {result.stderr}")
            counts[kind] += 1
            index += 1
        self.assertGreaterEqual(counts["PY"], 5)
        self.assertGreaterEqual(counts["OUTER"], 4)

    def test_shell_syntax_and_two_node_bound(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("#SBATCH --nodes=2", text)
        self.assertIn('"${SLURM_JOB_NUM_NODES:-0}" == 2', text)

    def test_generation_is_published_only_after_all_gates(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        gate = 'if [[ "$baseline_code" -eq 0 && "$extension_code" -eq 0 && "$finalize_code" -eq 0 ]]'
        self.assertIn(gate, text)
        tail = text.split(gate, 1)[1]
        self.assertIn('mv -f -- "$marker" "$GENERATION/READY"', tail)
        self.assertIn('"$PRIOR_RUN/current_generation.json"', tail)
        self.assertNotIn('mv -f -- "$temporary" "$PRIOR_SIF"', text)

    def test_only_reviewed_nodes_can_change_and_tests_are_targeted(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for node in ("M003.1:start", "M003.1:end", "M003.3:start", "M010:start"):
            self.assertIn(f"--allowed-changed-node {node}", text)
        self.assertIn('audit_node_reuse.py', text)
        self.assertIn('--only-node "$node"', text)
        self.assertNotIn('hydrate_dubbo_runtime.sh', text)

    def test_orphaned_tests_are_rejected_before_reuse_or_execution(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        audit_call = text.index('python3 "$CODE_ROOT/audit_executable_test_ownership.py"')
        reuse_call = text.index('python3 "$CODE_ROOT/audit_node_reuse.py"')
        runner_call = text.index('python3 "$CODE_ROOT/run_node_tests.py"')
        self.assertLess(audit_call, reuse_call)
        self.assertLess(audit_call, runner_call)


if __name__ == "__main__":
    unittest.main()
