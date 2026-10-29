# Part 9 acceptance coverage and integration handoff

The IDs below come from section 18 of the user-provided four-person roadmap.
“Backend verified” means the backend contract passed with disposable PostgreSQL
and synthetic integration peers. It is not a claim that the other members' live
services or the browser workflow have passed. The default backend suite contains
316 passing tests, zero failures/errors/skips; see [evidence](evidence/part-9-tests.json).

| ID | Scenario | Backend evidence | Remaining integration work |
| --- | --- | --- | --- |
| AT-01 | Text onboarding | `test_onboarding_requires_confirmed_ehr_success_and_persists`, managed recipes and persisted ordered steps | Agent intent/planning and browser request-to-result flow |
| AT-02 | Document upload/extraction | `test_extraction_lifecycle_is_persistent_and_validated`, upload/access/size/format/worker ownership tests | Actual OCR/vision evaluation and frontend field/validation display |
| AT-03 | EHR retrieval | `test_real_stdio_handshake_discovery_and_two_capabilities`, `test_real_http_transport_uses_server_side_bearer_token`, persisted tool trace tests | Run against the selected members' hospital simulators |
| AT-04 | Graph-dependent claim | `test_graph_dependent_claim_requires_all_three_queries`, `test_graph_revision_is_bound_to_claim_approval` | Live graph facade/NebulaGraph queries and agent reasoning |
| AT-05 | Automatic low-risk read | Allowlisted read execution, role checks, schema validation and real SDK read tests | Agent/UX demonstration using the same backend boundary |
| AT-06 | High-risk approval pause | `test_claim_requires_administrator_even_if_medium_is_automatic`, positive claim evidence pauses before submission | Agent WAITING_FOR_APPROVAL and staff approval UI |
| AT-07 | Approval granted | `test_review_execute_and_receipt_are_persisted_once` for patient creation and claim submission | Live MCP approval-token verification and durable receipt/idempotency storage |
| AT-08 | Approval rejected | `test_rejection_is_terminal_and_never_calls_write` | Frontend rejection flow and agent terminal-state handling |
| AT-09 | MCP failure | Timeout/schema/outage/mismatched-evidence tests; `test_uncertain_write_is_never_retried_or_recreated` | Owners' simulator failure injection and full UI recovery messages |
| AT-10 | Retry without duplicate action | Concurrent creation/dispatch/decision tests; `test_successful_receipt_reconciles_without_replaying_write` | Verify durable deduplication in actual selected simulators |
| AT-11 | Auditor trace | `test_trace_includes_history_failures_approval_and_recovery`, scoped paginated audit/trace/event APIs | Frontend auditor timeline consuming these APIs |
| AT-12 | Unauthorized approval | `test_only_independent_authorized_reviewer_can_approve`, stale/expired/tampered-scope tests | Agent cannot bypass backend dispatch; browser role scenarios |

The Part 9 backend responsibilities are verified locally: unit/API/security tests,
RBAC/idempotency/validation/constraints, real backend Compose startup/health,
persistence/outage recovery, and measured simple-read latency against the agreed
provisional target. The complete cross-team integration checkpoint remains open
for the items above. No integrated MVP tag is created at this stage: that tag must
follow a successful browser → backend → agent → selected MCP/graph acceptance run.

The old `tests/test_mcp_patient_tools.py` still targets the legacy agent/MCP APIs,
local seeded EHR and older unapproved clinical writes. It is unchanged and is not
part of the backend-only default `pytest` selection. Its owner must update and run
it as part of integration; excluding it is not evidence that it passes. Production
MCP, AI-agent, frontend and graph-implementation directories are unchanged here.

Use [Part 7](part-7.md) for action signing/review, [Part 8](part-8.md) for receipt
lookup and trace contracts, and [Part 9](part-9.md) for reproducible verification.
