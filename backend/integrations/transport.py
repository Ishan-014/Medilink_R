"""Official MCP client transports. No imports from backend.mcp or backend.agents."""

import os
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx

from backend.core.config import PROJECT_ROOT
from backend.integrations.errors import IntegrationError


class MCPTransport:
    @asynccontextmanager
    async def session(self, server):
        # Import on use: app startup/health does not launch hospital integrations.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        if server.transport == "stdio":
            if any(name not in os.environ for name in server.env_vars):
                raise IntegrationError("MCP_CONFIG_INVALID")
            env = {name: os.environ[name] for name in server.env_vars}
            cwd = PROJECT_ROOT / server.cwd if server.cwd else PROJECT_ROOT
            params = StdioServerParameters(command=server.command, args=server.args, env=env, cwd=str(cwd))
            with open(os.devnull, "w") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=server.timeout_seconds)) as client:
                        yield client
        else:
            headers = {}
            if server.bearer_token_env:
                token = os.environ.get(server.bearer_token_env)
                if not token:
                    raise IntegrationError("MCP_CONFIG_INVALID")
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(headers=headers, timeout=server.timeout_seconds,
                                         follow_redirects=False, trust_env=False) as http:
                async with streamable_http_client(server.url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=server.timeout_seconds)) as client:
                        yield client
