from datetime import datetime, timedelta, timezone

import jwt

from backend.integrations.errors import IntegrationError


def action_key(settings):
    secret = settings.action_signing_secret
    if not secret or len(secret) < 32 or secret.startswith(("dev-", "replace-")):
        raise IntegrationError("ACTION_SIGNING_NOT_CONFIGURED")
    return secret


def execution_context(settings, approval, call_id, actor_id, operation, argument_digest, idempotency_key, timeout):
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "iss": "medilink-actions", "aud": "medilink-mcp", "sub": actor_id,
        "token_type": "approved_action", "jti": call_id,
        "workflow_id": str(approval["workflow_id"]), "step_id": str(approval["workflow_step_id"]),
        "approval_id": str(approval["id"]), "operation": operation,
        "arguments_digest": argument_digest, "idempotency_key": idempotency_key,
        "iat": now, "exp": now + timedelta(seconds=timeout + 60),
    }, action_key(settings), algorithm="HS256")
    return {"medilink": {"approval_token": token, "idempotency_key": idempotency_key}}
