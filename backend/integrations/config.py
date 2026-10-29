"""Operator-owned MCP connection configuration. Never supplied in API requests."""

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.integrations.errors import IntegrationError

ServerName = Literal["ehr", "lab", "scheduling", "insurance", "billing"]
SERVER_NAMES = ("ehr", "lab", "scheduling", "insurance", "billing")


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    transport: Literal["stdio", "streamable_http"]
    command: str | None = Field(default=None, min_length=1, max_length=1024, repr=False)
    args: list[str] = Field(default_factory=list, max_length=32, repr=False)
    cwd: Path | None = Field(default=None, repr=False)
    url: str | None = Field(default=None, max_length=2048, repr=False)
    bearer_token_env: str | None = Field(default=None, pattern=r"^[A-Z_][A-Z0-9_]*$", repr=False)
    env_vars: list[str] = Field(default_factory=list, max_length=32, repr=False)
    timeout_seconds: float = Field(default=15, ge=0.1, le=120, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_transport(self):
        if self.transport == "stdio":
            if not self.command or self.url or self.bearer_token_env:
                raise ValueError("stdio requires a command and no HTTP settings")
            if any(len(arg) > 4096 for arg in self.args):
                raise ValueError("Command arguments are too long")
        else:
            if not self.url or self.command or self.args or self.cwd or self.env_vars:
                raise ValueError("HTTP requires a URL and no process settings")
            url = urlsplit(self.url)
            if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
                    or url.query or url.fragment or any(ord(c) < 33 for c in self.url)):
                raise ValueError("Invalid MCP endpoint")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote MCP endpoints require HTTPS")
        if any(re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) is None for name in self.env_vars):
            raise ValueError("Invalid process environment names")
        return self


class IntegrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    servers: dict[ServerName, ServerConfig] = Field(default_factory=dict, max_length=5)


def load_config(settings) -> IntegrationConfig:
    if settings.mcp_config_file is None:
        return IntegrationConfig()
    try:
        path = Path(settings.mcp_config_file)
        if path.stat().st_size > 65536:
            raise ValueError("Configuration too large")
        return IntegrationConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        raise IntegrationError("MCP_CONFIG_INVALID") from exc


def configured_server(settings, name: str) -> ServerConfig:
    server = load_config(settings).servers.get(name)
    if server is None:
        raise IntegrationError("MCP_NOT_CONFIGURED")
    return server
