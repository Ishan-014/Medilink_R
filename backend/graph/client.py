"""Bounded HTTP facade for the graph owner's NebulaGraph-backed service."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from backend.graph.contracts import GraphRequest, GraphResult

ERRORS = {
    "GRAPH_NOT_CONFIGURED": (503, "Graph service is not configured"),
    "GRAPH_CONFIG_INVALID": (503, "Graph service configuration is invalid"),
    "GRAPH_UNAVAILABLE": (503, "Graph service is unavailable"),
    "GRAPH_TIMEOUT": (504, "Graph service timed out"),
    "GRAPH_RESULT_INVALID": (502, "Graph service returned invalid evidence"),
    "GRAPH_INCOMPLETE": (502, "Graph service returned incomplete evidence"),
    "GRAPH_CANCELLED": (503, "Graph query was cancelled"),
}


class GraphError(HTTPException):
    def __init__(self, code):
        status, message = ERRORS[code]
        super().__init__(status, message)
        self.error_code = code


def endpoint(settings):
    value = settings.graph_service_url
    if not value:
        raise GraphError("GRAPH_NOT_CONFIGURED")
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError
        if url.hostname not in {"127.0.0.1", "localhost", "::1"} and (url.scheme != "https" or not settings.graph_service_token):
            raise ValueError
    except ValueError as exc:
        raise GraphError("GRAPH_CONFIG_INVALID") from exc
    return value.rstrip("/")


def validate_result(request, value):
    try:
        if isinstance(value, GraphResult):
            value = value.model_dump(mode="json")
        result = GraphResult.model_validate(value)
    except (ValidationError, ValueError, TypeError) as exc:
        raise GraphError("GRAPH_RESULT_INVALID") from exc
    if (result.query_id != request.query_id or result.operation != request.operation
            or result.patient_id != request.patient_id or len(result.items) > request.max_results):
        raise GraphError("GRAPH_RESULT_INVALID")
    if not result.complete:
        raise GraphError("GRAPH_INCOMPLETE")
    return result


class GraphClient:
    def __init__(self, http_client_factory=None):
        self.http_client_factory = http_client_factory or httpx.AsyncClient

    async def _request(self, settings, method, suffix, body=None):
        base = endpoint(settings)
        headers = {"Accept": "application/json"}
        if settings.graph_service_token:
            headers["Authorization"] = f"Bearer {settings.graph_service_token}"
        try:
            async with asyncio.timeout(settings.graph_timeout_seconds):
                async with self.http_client_factory(headers=headers, timeout=settings.graph_timeout_seconds,
                                                    follow_redirects=False, trust_env=False) as client:
                    async with client.stream(method, base + suffix, json=body) as response:
                        if response.status_code != 200:
                            raise GraphError("GRAPH_UNAVAILABLE")
                        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                            raise GraphError("GRAPH_RESULT_INVALID")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 1024 * 1024:
                                raise GraphError("GRAPH_RESULT_INVALID")
                        return json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        except GraphError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise GraphError("GRAPH_TIMEOUT") from exc
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise GraphError("GRAPH_RESULT_INVALID") from exc
        except Exception as exc:
            raise GraphError("GRAPH_UNAVAILABLE") from exc

    async def health(self, settings):
        value = await self._request(settings, "GET", "/health")
        if value != {"status": "ready"}:
            raise GraphError("GRAPH_UNAVAILABLE")
        return {"status": "ready"}

    async def query(self, settings, request: GraphRequest) -> GraphResult:
        value = await self._request(settings, "POST", "/queries", request.model_dump(mode="json"))
        return validate_result(request, value)
