"""Synthetic MCP protocol test peer; never imports the project hospital servers."""

import argparse
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

server = FastMCP("Backend protocol fixture", log_level="ERROR", json_response=True, stateless_http=True)


@server.tool()
def get_patient(patient_id: str) -> dict[str, str]:
    return {"patient_id": patient_id, "source": "synthetic-ehr"}


@server.tool()
def get_results(patient_id: str) -> dict[str, str]:
    return {"patient_id": patient_id, "source": "synthetic-lab"}


class RequireTestToken:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and dict(scope.get("headers", [])).get(b"authorization") != b"Bearer synthetic-test-token":
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-port", type=int)
    args = parser.parse_args()
    if args.http_port:
        app = server.streamable_http_app()
        app.add_middleware(RequireTestToken)
        uvicorn.run(app, host="127.0.0.1", port=args.http_port, log_level="error")
    else:
        # Only this explicit harmless environment value may be inherited in tests.
        assert "PRIVATE_BACKEND_TEST_SECRET" not in os.environ
        server.run(transport="stdio")
