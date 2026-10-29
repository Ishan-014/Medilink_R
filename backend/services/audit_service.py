"""Bounded reference-only audit and trace queries with existing workflow scope."""

from psycopg2.extras import RealDictCursor
from backend.db.connection import connection
from backend.services.workflow_service import load, serialize


def list_records(
    settings,
    table,
    *,
    limit,
    offset,
    workflow_id=None,
    actor_user_id=None,
    code=None,
    outcome=None,
    after=None,
    before=None,
):
    assert table in {"audit_logs", "system_events"}
    clauses = []
    values = []
    for field, value in (
        ("workflow_id", workflow_id),
        ("actor_user_id", actor_user_id),
        ("code", code),
        ("outcome", outcome),
    ):
        if value is not None:
            clauses.append(f"{field}=%s")
            values.append(str(value))
    for op, value in ((">=", after), ("<", before)):
        if value is not None:
            clauses.append(f"created_at {op} %s")
            values.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"SELECT * FROM {table}"
                + where
                + " ORDER BY created_at,id LIMIT %s OFFSET %s",
                (*values, limit + 1, offset),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            return {
                "items": rows[:limit],
                "next_offset": offset + limit if len(rows) > limit else None,
            }


def trace(settings, workflow_id, user, limit, offset):
    # One consistent snapshot across workflow summary and paged history.
    with connection(settings, isolation_level="REPEATABLE READ") as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            workflow = load(cursor, workflow_id, user)
            summary = serialize(cursor, workflow)
            cursor.execute(
                """SELECT * FROM (
                SELECT a.id,a.created_at,'audit' AS source,a.actor_user_id,a.request_id,
                    a.action,a.resource_type,a.resource_id,a.outcome,
                    a.metadata || CASE WHEN a.resource_type='approval_events' THEN
                        jsonb_build_object('reason',(SELECT reason FROM approval_events WHERE id=a.resource_id::uuid))
                        ELSE '{}'::jsonb END AS metadata
                FROM audit_logs a WHERE a.workflow_id=%s OR (a.resource_type='documents' AND a.resource_id IN
                    (SELECT document_id::text FROM workflow_documents WHERE workflow_id=%s))
                UNION ALL
                SELECT e.id,e.created_at,'system',e.actor_user_id,e.request_id,e.code,'system_events',e.id::text,
                    CASE WHEN e.http_status IN (401,403) THEN 'denied'
                        WHEN e.severity IN ('warning','error') THEN 'failure' ELSE 'success' END,
                    jsonb_build_object('component',e.component,'severity',e.severity,'http_status',e.http_status,'duration_ms',e.duration_ms)
                FROM system_events e WHERE e.workflow_id=%s
            ) timeline ORDER BY created_at,id LIMIT %s OFFSET %s""",
                (workflow_id, workflow_id, workflow_id, limit + 1, offset),
            )
            events = [dict(row) for row in cursor.fetchall()]
            return {
                "workflow": summary.model_dump(mode="json"),
                "events": events[:limit],
                "next_offset": offset + limit if len(events) > limit else None,
            }
