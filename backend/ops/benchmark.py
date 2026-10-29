"""Reproducible loopback read benchmark in a uniquely created disposable DB.

Requires explicit MEDILINK_BENCHMARK_ADMIN_DSN with CREATE DATABASE permission.
Only the generated medilink_bench_<uuid> database is created and dropped.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import secrets
import socket
import statistics
from tempfile import TemporaryDirectory
from threading import Thread
import time
from uuid import uuid4

import httpx
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values
import uvicorn

from backend.application import create_app
from backend.auth.security import PASSWORD_HASH, issue_access_token
from backend.core.config import Settings
from backend.db.connection import connection
from backend.db.migrate import migrate


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def measure(client, path, headers, requests, concurrency, target):
    for _ in range(5):
        response = client.get(path, headers=headers)
        if response.status_code != 200:
            raise RuntimeError("Warmup read failed")

    def read(_):
        start = time.perf_counter()
        response = client.get(path, headers=headers)
        return (time.perf_counter() - start) * 1000, response.status_code

    start = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        samples = list(pool.map(read, range(requests)))
    elapsed = time.perf_counter() - start
    latencies = [duration for duration, _ in samples]
    failures = sum(status != 200 for _, status in samples)
    p95 = percentile(latencies, 0.95)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "failures": failures,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "max_ms": round(max(latencies), 3),
        "requests_per_second": round(requests / elapsed, 2),
        "passes": failures == 0 and p95 <= target,
    }


def run(admin_dsn, *, requests=100, concurrency=8, workflows=200, steps=5, target=500):
    params = psycopg2.extensions.parse_dsn(admin_dsn)
    database_name = "medilink_bench_" + uuid4().hex
    admin = psycopg2.connect(admin_dsn)
    admin.autocommit = True
    created = False
    server = None
    thread = None
    sock = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(database_name)
                )
            )
            created = True
        with TemporaryDirectory(prefix="medilink-bench-docs-") as storage:
            settings = Settings(
                db_name=database_name,
                db_host=params.get("host", "localhost"),
                db_port=int(params.get("port", 5432)),
                db_user=params.get("user", "postgres"),
                db_password=params.get("password"),
                jwt_secret=secrets.token_urlsafe(48),
                document_storage_dir=Path(storage),
            )
            with connection(settings) as conn:
                migrate(conn)
                with conn.cursor() as cursor:
                    cursor.execute("SHOW server_version")
                    postgres_version = cursor.fetchone()[0]
                    cursor.execute(
                        """INSERT INTO users(full_name,email,password_hash,role)
                        VALUES ('Synthetic Benchmark','benchmark@example.com',%s,'patient') RETURNING user_id::text""",
                        (PASSWORD_HASH.hash(secrets.token_urlsafe(24)),),
                    )
                    user = {
                        "user_id": cursor.fetchone()[0],
                        "email": "benchmark@example.com",
                        "role": "patient",
                    }
                    ids = [str(uuid4()) for _ in range(workflows)]
                    execute_values(
                        cursor,
                        "INSERT INTO workflow_runs(id,owner_user_id,workflow_type,request) VALUES %s",
                        [
                            (
                                identifier,
                                user["user_id"],
                                "onboarding",
                                "Synthetic benchmark fixture",
                            )
                            for identifier in ids
                        ],
                    )
                    execute_values(
                        cursor,
                        "INSERT INTO workflow_steps(id,workflow_id,position,action,risk) VALUES %s",
                        [
                            (
                                str(uuid4()),
                                identifier,
                                position,
                                "ehr.get_patient",
                                "low",
                            )
                            for identifier in ids
                            for position in range(steps)
                        ],
                    )
            headers = {
                "Authorization": "Bearer "
                + issue_access_token(user, auth_level=1, settings=settings)
            }
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(settings), log_level="error", access_log=False
                )
            )
            thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            deadline = time.monotonic() + 15
            while (
                not server.started and thread.is_alive() and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            if not server.started:
                raise RuntimeError("Benchmark HTTP server did not start")
            endpoints = {
                "identity": "/api/me",
                "workflow_list_50": "/api/workflows?limit=50",
                "workflow_detail": f"/api/workflows/{ids[0]}",
                "workflow_trace_50": f"/api/workflows/{ids[0]}/trace?limit=50",
            }
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=15,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=concurrency, max_keepalive_connections=concurrency
                ),
            ) as client:
                results = {
                    name: measure(client, path, headers, requests, concurrency, target)
                    for name, path in endpoints.items()
                }
            return {
                "target": {
                    "p95_ms": target,
                    "classification": "user-approved provisional target; not a confirmed SRS value",
                },
                "transport": "loopback HTTP, one Uvicorn worker, persistent HTTPX connection pool",
                "environment": {
                    "python": platform.python_version(),
                    "os": platform.system(),
                    "postgresql": postgres_version,
                    "fastapi": version("fastapi"),
                    "uvicorn": version("uvicorn"),
                },
                "dataset": {
                    "workflows": workflows,
                    "steps_per_workflow": steps,
                    "users": 1,
                },
                "warmup_requests_per_endpoint": 5,
                "results": results,
                "passes": all(item["passes"] for item in results.values()),
            }
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(10)
        if sock:
            sock.close()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
        admin.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--workflows", type=int, default=200)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--target-p95-ms", type=float, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not (
        10 <= args.requests <= 10000
        and 1 <= args.concurrency <= 32
        and 50 <= args.workflows <= 5000
        and 1 <= args.steps <= 50
        and math.isfinite(args.target_p95_ms)
        and args.target_p95_ms > 0
    ):
        parser.error("Invalid benchmark dimensions or latency target")
    dsn = os.getenv("MEDILINK_BENCHMARK_ADMIN_DSN")
    if not dsn:
        parser.error(
            "Set MEDILINK_BENCHMARK_ADMIN_DSN explicitly to permit creating a disposable benchmark database"
        )
    try:
        report = run(
            dsn,
            requests=args.requests,
            concurrency=args.concurrency,
            workflows=args.workflows,
            steps=args.steps,
            target=args.target_p95_ms,
        )
    except Exception:
        parser.exit(
            1,
            "Benchmark failed; check local database access and benchmark prerequisites.\n",
        )
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
