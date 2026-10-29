"""Bounded MCP sessions with checked contracts and JSON-only results."""

import asyncio
import json

import httpx
from jsonschema import validators
from referencing import Registry

from backend.integrations.errors import IntegrationError
from backend.integrations.transport import MCPTransport


def checked_json(value, max_bytes, error_code):
    try:
        encoded = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > max_bytes:
            raise ValueError("Payload too large")
        return encoded
    except (TypeError, ValueError, RecursionError) as exc:
        raise IntegrationError(error_code) from exc


def schema_validator(schema):
    checked_json(schema, 65536, "MCP_SCHEMA_INVALID")

    def check_refs(value, depth=0):
        if depth > 30:
            raise ValueError("Schema too deep")
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#")):
                    raise ValueError("External schema references are disabled")
                check_refs(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check_refs(child, depth + 1)
    try:
        if not isinstance(schema, dict):
            raise ValueError("Object schema required")
        check_refs(schema)
        cls = validators.validator_for(schema)
        cls.check_schema(schema)
        return cls(schema, registry=Registry())
    except Exception as exc:
        raise IntegrationError("MCP_SCHEMA_INVALID") from exc


def matches(validator, value, code):
    try:
        if not validator.is_valid(value):
            raise ValueError("Schema mismatch")
    except Exception as exc:
        raise IntegrationError(code) from exc


def normalized_error(exc, dispatched):
    if isinstance(exc, IntegrationError):
        exc.dispatched = dispatched
        return exc
    leaves = []
    def walk(error):
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:
                walk(child)
        else:
            leaves.append(error)
    walk(exc)
    if any(isinstance(e, (TimeoutError, httpx.TimeoutException)) or
           getattr(getattr(e, "error", None), "code", None) == -32001 for e in leaves):
        code = "MCP_TIMEOUT"
    elif dispatched and any(isinstance(e, RuntimeError) for e in leaves):
        code = "MCP_OUTPUT_INVALID"
    else:
        code = "MCP_UNAVAILABLE"
    return IntegrationError(code, dispatched=dispatched)


class HospitalClient:
    def __init__(self, transport=None):
        self.transport = transport or MCPTransport()

    async def health(self, server):
        try:
            async with asyncio.timeout(server.timeout_seconds):
                async with self.transport.session(server) as session:
                    await session.initialize()
                    await session.send_ping()
                    listing = await session.list_tools()
                    if listing.nextCursor or len(listing.tools) > 128:
                        raise IntegrationError("MCP_SCHEMA_INVALID")
                    for tool in listing.tools:
                        schema_validator(tool.inputSchema)
                        schema_validator(tool.outputSchema)
                    return [tool.name for tool in listing.tools]
        except Exception as exc:
            raise normalized_error(exc, False) from exc

    async def call(self, server, tool_name, arguments):
        dispatched = False
        try:
            checked_json(arguments, 65536, "MCP_INPUT_INVALID")
            async with asyncio.timeout(server.timeout_seconds):
                async with self.transport.session(server) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    if listing.nextCursor or len(listing.tools) > 128:
                        raise IntegrationError("MCP_SCHEMA_INVALID")
                    tools = [tool for tool in listing.tools if tool.name == tool_name]
                    if len(tools) != 1:
                        raise IntegrationError("MCP_TOOL_UNAVAILABLE")
                    tool = tools[0]
                    incoming = schema_validator(tool.inputSchema)
                    outgoing = schema_validator(tool.outputSchema)
                    matches(incoming, arguments, "MCP_INPUT_INVALID")
                    dispatched = True
                    response = await session.call_tool(tool_name, arguments)
                    if response.isError:
                        raise IntegrationError("MCP_REMOTE_ERROR")
                    result = response.structuredContent
                    if result is None:
                        raise IntegrationError("MCP_OUTPUT_INVALID")
                    checked_json(result, 1024 * 1024, "MCP_OUTPUT_INVALID")
                    matches(outgoing, result, "MCP_OUTPUT_INVALID")
                    return result
        except Exception as exc:
            raise normalized_error(exc, dispatched) from exc
