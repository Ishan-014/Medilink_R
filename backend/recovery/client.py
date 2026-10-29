"""Bounded lookup of durable external receipts; never repeats an external write."""

import json
from urllib.parse import urlsplit

import httpx

from backend.core.errors import BackendError
from backend.recovery.contracts import LookupRequest, LookupResult


def validate_config(settings):
    raw = settings.recovery_service_url
    if not raw:
        raise BackendError("RECOVERY_NOT_CONFIGURED")
    try:
        url = urlsplit(raw)
        url.port
    except ValueError as exc:
        raise BackendError("RECOVERY_NOT_CONFIGURED") from exc
    if (
        len(raw) > 2048
        or url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or any(ord(c) < 33 for c in raw)
    ):
        raise BackendError("RECOVERY_NOT_CONFIGURED")
    if url.hostname not in {"localhost", "127.0.0.1", "::1"} and (
        url.scheme != "https" or not settings.recovery_service_token
    ):
        raise BackendError("RECOVERY_NOT_CONFIGURED")
    return raw.rstrip("/")


class ReconciliationClient:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory or httpx.AsyncClient

    async def lookup(self, settings, request):
        url = validate_config(settings)
        headers = (
            {"Authorization": f"Bearer {settings.recovery_service_token}"}
            if settings.recovery_service_token
            else {}
        )
        try:
            async with self.client_factory(
                timeout=settings.recovery_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    url + "/action-receipts/lookup",
                    json=request.model_dump(mode="json"),
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        raise BackendError("RECOVERY_UNAVAILABLE")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 65536:
                            raise BackendError("RECOVERY_OUTPUT_INVALID")
            result = LookupResult.model_validate(
                json.loads(
                    content,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            )
            if (
                result.model_dump(include=set(LookupRequest.model_fields))
                != request.model_dump()
            ):
                raise BackendError("RECOVERY_OUTPUT_INVALID")
            return result
        except BackendError:
            raise
        except httpx.TimeoutException as exc:
            raise BackendError("RECOVERY_TIMEOUT") from exc
        except (ValueError, TypeError, RecursionError) as exc:
            raise BackendError("RECOVERY_OUTPUT_INVALID") from exc
        except Exception as exc:
            raise BackendError("RECOVERY_UNAVAILABLE") from exc
