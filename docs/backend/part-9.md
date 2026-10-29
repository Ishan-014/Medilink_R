# Backend Part 9: testing and performance hardening

Based on `backend-part-8`. This completes the backend owner's local Part 9 work:
security/API/database verification, backend-only Compose startup/health checks,
and reproducible measurements of simple reads. Other members' implementations
remain separate and will connect through the existing contracts. Cross-team
acceptance status is documented in [the AT-01–AT-12 matrix](part-9-acceptance.md).

## What changed

- Workflow list reads fetch all steps for the selected page in a single query.
  A nonempty page now uses two service queries, rather than one plus one per
  workflow (up to 51 for a 50-item page). Ownership filtering, pagination and
  per-workflow step order are preserved. The query-count/scope regression test
  exercises both populated and empty pages.
- Added negative-token, unauthenticated-route, registration-race, input/filter
  injection, request-ID and container health-probe checks. Existing approval,
  idempotency, validation, database-constraint and failure-injection tests remain.
- Added a backend-only non-root image, PostgreSQL/migration/backend Compose stack,
  bounded readiness probe and isolated startup/persistence/outage smoke command.
- Added a disposable-database real-HTTP benchmark and backend GitHub Actions
  workflow, including non-skipped database tests and a separate Compose smoke job.

No API shapes or routes change. Existing migrations `0001`–`0008` are preserved;
there is no new database migration for this part. Backend operations utilities
live in `backend/ops`; they do not call an LLM or project MCP/agent implementation.

## Backend tests

From the repository root:

```bash
python -m pip install -r backend/requirements-test.txt
# Set this only to an EMPTY DISPOSABLE test database, never your application DB.
export MEDILINK_TEST_DB_DSN='dbname=medilink_test host=localhost user=postgres password=your-test-password'
python -m pytest -q --junitxml=artifacts/backend-tests.xml
```

PowerShell uses `$env:MEDILINK_TEST_DB_DSN = '...'` instead of `export`.
`pytest.ini` makes `tests/backend` the default. Database fixtures refuse existing
public tables and clean up their isolated data. Without a test DSN the DB tests
skip, which is incomplete verification. CI sets the DSN and rejects skipped tests.
The old agent/MCP suite is a documented integration exception, not silently treated
as passing; see the acceptance matrix.

Local result: **316 passed, zero failures/errors/skips** on Python 3.14.4 and
PostgreSQL 18.6. [Machine-readable summary](evidence/part-9-tests.json).
One existing Starlette/httpx deprecation warning remains. The CI workflow uses
Python 3.12 with PostgreSQL 16; configuring it is not a claim that a hosted CI run
has completed. The actual Compose runtime below was also verified locally.

Lint the backend-owned boundary:

```bash
ruff check --select F backend/api backend/core backend/db backend/graph backend/integrations backend/observability backend/ops backend/recovery backend/risk backend/services backend/application.py backend/auth/security.py tests/backend
```

## Compose startup

Copy `.env.example` to your local `.env`, then configure a local-only database
password and random JWT/card secrets. Generate each secret separately with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`. Configure the
separate action-signing secret before enabling approved hospital writes.

```bash
docker compose -f compose.backend.yaml config --quiet
docker compose -f compose.backend.yaml up --build --detach --wait
docker compose -f compose.backend.yaml ps --all
```

The stack provides PostgreSQL 16 on its private project network, a one-shot
migration service gated on database health, then the API gated on successful
migration completion. `backend` runs as UID 10001, drops capabilities, forbids
privilege escalation, uses a read-only image filesystem and keeps private uploads
in a named volume. `/tmp` is a temporary filesystem. The image build context
excludes frontend, project agents/MCP, local `.env`/MCP configuration, uploaded and
biometric data. This is the `backend.application:create_app` API image; it does
not run the legacy AI/dashboard `backend.main:app` entry point.

The API binds to `127.0.0.1:8000` by default; set `MEDILINK_API_PORT` if occupied.
PostgreSQL is not published on a host port. Docker health uses the bounded
`python -m backend.ops.healthcheck` probe against `/api/health/ready`.
Readiness checks database connectivity; migration ordering is enforced separately
by Compose. Health does not assert that MCP, graph, OCR or AI services are ready.

Check `http://127.0.0.1:8000/api/health`, `/api/health/ready` and `/docs`.
Stop with `docker compose -f compose.backend.yaml down`; named database/document
volumes are retained. Existing database passwords are not changed by editing the
`.env` after first initialization. Use a deliberate credential rotation rather
than deleting an application volume. The development database/migration role is
not a production least-privilege deployment.

When other members integrate, supply container-reachable endpoints and mount
operator-owned configuration explicitly using a Compose override. A container's
`localhost` is itself; it is not another member's host service. See the existing
Part 4/6/7/8 transport, graph, signing and receipt contracts. No unimplemented
agent/MCP/frontend service is included in this backend stack.

## Isolated Compose verification

With Docker running:

```bash
python -m backend.ops.compose_smoke --output artifacts/backend-compose.json
```

The host command needs HTTPX; the backend image installs its foundation dependencies.
It creates a uniquely named test project with generated synthetic credentials and
an unused loopback port, builds the image, then verifies:

1. Database health, successful migrations and healthy backend startup.
2. UID 10001 and absence of project agents/MCP, frontend and `.env` from the image.
3. Real patient registration/login, workflow creation and private text upload.
4. Workflow and document persistence after restarting the backend container.
5. Readiness 503 during database outage while liveness stays 200, then read recovery
   after restarting the database.
6. Migration status remains applied without replaying SQL.

It removes only its generated test project's containers/network/volumes in
`finally`. It never runs `down --volumes` on the application project. Developer
`MEDILINK_*` and `COMPOSE_*` environment values cannot override its synthetic
settings. Output contains only verification metadata, not passwords or tokens.
On WSL without native Docker integration, `--docker /path/to/docker.exe` supports
the existing Windows Docker Desktop engine via converted host paths.

[Recorded Compose evidence](evidence/part-9-compose.json) includes the verified
runtime versions. Docker Desktop was used locally; this does not certify all
possible hosts, optional dependencies or live integrations.

## Read benchmark

The provided plan documents refer to an SRS response-time target without giving a
number. The user approved **500 ms at p95 as a provisional simple-read target**.
It must not be represented as a confirmed SRS value.

Run with an explicit local administrative DSN that may create databases:

```bash
export MEDILINK_BENCHMARK_ADMIN_DSN='dbname=postgres host=localhost user=postgres password=your-test-password'
python -m backend.ops.benchmark --requests 100 --concurrency 8 --workflows 200 --steps 5 --target-p95-ms 500 --output artifacts/backend-benchmark.json
```

The command creates a unique `medilink_bench_<uuid>` database, applies migrations,
seeds one synthetic patient with 200 workflows/1000 steps, serves the real app over
loopback HTTP with one Uvicorn worker, and measures authenticated reads using
persistent HTTPX connections. Five warmup calls per endpoint precede 100 measured
requests at concurrency 8. It drops only its generated database afterward, even on
failure. No real patients, external systems or application tables are modified.

Recorded local results ([full JSON](evidence/part-9-benchmark.json)):

| Read | p95 ms | Failed requests | Provisional target |
| --- | ---: | ---: | --- |
| Current identity | 63.705 | 0/100 | Pass |
| Workflow list, 50 items | 87.704 | 0/100 | Pass |
| Workflow detail | 73.097 | 0/100 | Pass |
| Workflow trace, up to 50 events | 76.473 | 0/100 | Pass |

These are local loopback measurements on Python 3.14.4/PostgreSQL 18.6, not Docker
network, production load/capacity or full agent/MCP latency claims. The artifact
records versions, dataset, warmup, concurrency, percentiles and failures. Change
`--target-p95-ms` once the team supplies the actual SRS threshold. Timing limits
are exercised by the explicit benchmark, rather than flaky wall-clock unit tests.

## CI and remaining team checkpoint

`.github/workflows/backend.yml` runs backend lint, the complete backend test suite
with a disposable PostgreSQL service, the 500 ms p95 provisional benchmark, and
an isolated Compose build/health/persistence/outage job. It uploads machine-readable
results. Actions are pinned to verified official v7 commits from
[checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python) and
[upload-artifact](https://github.com/actions/upload-artifact). No repository tag or release is created by the workflow.

Browser-to-agent-to-selected-MCP/graph acceptance, actual OCR/agent evaluation,
front-end role journeys and the stable integrated MVP tag remain cross-team work.
The acceptance matrix identifies the backend evidence and each outstanding owner
handoff. Frontend, project MCP, AI-agent and graph-implementation files remain
unchanged in this part.
