"""Only fixed codes, normalized routes, IDs and timing enter operational logs."""

import json
import logging
from uuid import UUID

from backend.db.connection import connection
from backend.api.errors import HTTP_CODES
from backend.observability.context import request_context

logger = logging.getLogger("medilink.events")


def component(code):
    for prefix, name in (
        ("DATABASE_", "database"),
        ("MCP_", "mcp"),
        ("GRAPH_", "graph"),
        ("AI_", "ai"),
        ("RECOVERY_", "recovery"),
    ):
        if code.startswith(prefix):
            return name
    return "api"


def finish_request(settings, request, status, duration_ms, code=None):
    code = code or (HTTP_CODES.get(status, "HTTP_ERROR") if status >= 400 else None)
    context = request_context.get()
    route = request.scope.get("route")
    route_template = getattr(route, "path", "unmatched")
    # Never log actual paths, query strings, headers, exception messages or bodies.
    record = {
        "event": "http_request",
        "request_id": context.request_id if context else None,
        "actor_user_id": context.actor_user_id if context else "anonymous",
        "method": request.method,
        "route": route_template,
        "status": status,
        "duration_ms": duration_ms,
        "code": code,
    }
    logger.info(json.dumps(record, separators=(",", ":")))
    if (
        status < 400
        or request.url.path.startswith("/api/health")
        or not request.url.path.startswith("/api/")
    ):
        return
    try:
        workflow_id = request.path_params.get("workflow_id")
        try:
            workflow_id = str(UUID(str(workflow_id))) if workflow_id else None
        except ValueError:
            workflow_id = None
        with connection(settings) as conn:
            with conn.cursor() as cursor:
                if workflow_id:
                    cursor.execute(
                        "SELECT 1 FROM workflow_runs WHERE id=%s", (workflow_id,)
                    )
                    if not cursor.fetchone():
                        workflow_id = None
                cursor.execute(
                    """INSERT INTO system_events
                    (workflow_id,request_id,actor_user_id,component,code,severity,http_status,duration_ms)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        workflow_id,
                        record["request_id"],
                        record["actor_user_id"],
                        component(code or "HTTP_ERROR"),
                        code or "HTTP_ERROR",
                        "error" if status >= 500 else "warning",
                        status,
                        duration_ms,
                    ),
                )
                if status in {401, 403}:
                    cursor.execute(
                        """INSERT INTO audit_logs
                        (workflow_id,actor_user_id,request_id,action,resource_type,resource_id,outcome)
                        VALUES (%s,%s,%s,'access.denied','api',%s,'denied')""",
                        (
                            workflow_id,
                            record["actor_user_id"],
                            record["request_id"],
                            route_template,
                        ),
                    )
    except Exception:
        # Telemetry loss must not change a request outcome or recursively log DB data.
        logger.warning(
            json.dumps(
                {"event": "telemetry_unavailable", "request_id": record["request_id"]}
            )
        )
