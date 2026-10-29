"""Run the backend's synthetic AT-01–AT-12 handoff rehearsal (test DB required).

Uses backend tests and fixture peers, not live MCP/graph/AI services or a browser.
No staff-authentication bypass is installed in the running application.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as E

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = {
    "AT-01": "test_business_workflows.py::test_onboarding_requires_confirmed_ehr_success_and_persists",
    "AT-02": "test_documents.py::test_extraction_lifecycle_is_persistent_and_validated",
    "AT-03": "test_mcp_client_protocol.py::test_real_stdio_handshake_discovery_and_two_capabilities",
    "AT-04": "test_graph.py::test_graph_dependent_claim_requires_all_three_queries",
    "AT-05": "test_integrations.py::test_two_capabilities_are_validated_and_traced",
    "AT-06": "test_approvals.py::test_claim_requires_administrator_even_if_medium_is_automatic",
    "AT-07": "test_approvals.py::test_review_execute_and_receipt_are_persisted_once",
    "AT-08": "test_approvals.py::test_rejection_is_terminal_and_never_calls_write",
    "AT-09": "test_business_workflows.py::test_timeout_never_confirms_a_patient_and_cannot_auto_retry",
    "AT-10": "test_audit_recovery.py::test_successful_receipt_reconciles_without_replaying_write",
    "AT-11": "test_audit_recovery.py::test_trace_includes_history_failures_approval_and_recovery",
    "AT-12": "test_approvals.py::test_only_independent_authorized_reviewer_can_approve",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not os.getenv("MEDILINK_TEST_DB_DSN"):
        parser.error(
            "Set MEDILINK_TEST_DB_DSN to an empty disposable test database; skipped scenarios are not a rehearsal"
        )
    with TemporaryDirectory(prefix="medilink-rehearsal-") as storage:
        junit = Path(storage) / "rehearsal.xml"
        nodes = ["tests/backend/" + node for node in dict.fromkeys(SCENARIOS.values())]
        try:
            process = subprocess.run(
                [sys.executable, "-m", "pytest", *nodes, "-q", f"--junitxml={junit}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=300,
            )
            suites = list(E.parse(junit).getroot().iter("testsuite"))
        except (OSError, subprocess.TimeoutExpired, E.ParseError):
            parser.exit(
                1,
                "Rehearsal could not finish. Run the selected backend tests directly to diagnose it.\n",
            )
        counts = {
            key: sum(int(s.attrib.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        executed = {
            case.attrib.get("name", "").split("[", 1)[0]
            for suite in suites
            for case in suite.iter("testcase")
        }
        covered = [
            identifier
            for identifier, node in SCENARIOS.items()
            if node.rsplit("::", 1)[1] in executed
        ]
        passed = (
            process.returncode == 0
            and counts["tests"] > 0
            and len(covered) == len(SCENARIOS)
            and all(counts[k] == 0 for k in ("failures", "errors", "skipped"))
        )
        report = {
            "scope": "synthetic backend handoff rehearsal; not a cross-team end-to-end demonstration",
            "counts": counts,
            "scenarios": SCENARIOS,
            "covered_acceptance_ids": covered,
            "passes": passed,
        }
    content = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
