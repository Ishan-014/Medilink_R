"""Durable reservations for future external-action adapters.

Commit a reservation BEFORE contacting the external system. Only the newly
reserved caller may perform the action; duplicates always return should_execute
False. Pending/uncertain actions need reconciliation, never an automatic retry.
This module does not perform external calls or grant approval/authorization.
"""

from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from backend.db.connection import connection
from backend.services.workflow_service import fingerprint, lock_key


@dataclass(frozen=True)
class Reservation:
    id: str
    status: str
    should_execute: bool
    result_reference: str | None = None


def reserve_action(settings, workflow_id: str, action: str, key: str, payload: dict) -> Reservation:
    if not action.strip() or len(action) > 200 or not key or len(key) > 128:
        raise ValueError("Invalid action or idempotency key")
    digest = fingerprint(payload)
    with connection(settings) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            lock_key(cursor, f"action:{workflow_id}:{action}:{key}")
            cursor.execute("""
                SELECT * FROM workflow_action_keys
                WHERE workflow_id = %s AND action = %s AND idempotency_key = %s
            """, (workflow_id, action, key))
            prior = cursor.fetchone()
            if prior:
                if prior["fingerprint"] != digest:
                    raise HTTPException(409, "Action idempotency key conflicts with an earlier request")
                return Reservation(str(prior["id"]), prior["status"], False, prior["result_reference"])
            action_id = str(uuid4())
            cursor.execute("""
                INSERT INTO workflow_action_keys (id, workflow_id, action, idempotency_key, fingerprint)
                VALUES (%s, %s, %s, %s, %s)
            """, (action_id, workflow_id, action, key, digest))
            return Reservation(action_id, "pending", True)


def record_action_outcome(settings, reservation_id: str, status: str, result_reference: str | None = None):
    if status not in {"succeeded", "failed", "uncertain"}:
        raise ValueError("Invalid action outcome")
    if status == "succeeded" and (not result_reference or not result_reference.strip()):
        raise ValueError("Success requires a confirmed result reference")
    with connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE workflow_action_keys SET status = %s, result_reference = %s,
                    updated_at = clock_timestamp() WHERE id = %s AND status = 'pending'
            """, (status, result_reference, reservation_id))
            if cursor.rowcount != 1:
                raise HTTPException(409, "Action is missing or already has an outcome; reconcile instead")
