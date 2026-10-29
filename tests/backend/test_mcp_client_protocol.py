import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.integrations.client import HospitalClient
from backend.integrations.config import ServerConfig
from backend.integrations.errors import IntegrationError

FIXTURE = Path(__file__).parent / "fixtures" / "hospital_tools.py"


def test_real_stdio_handshake_discovery_and_two_capabilities(monkeypatch):
    monkeypatch.setenv("PRIVATE_BACKEND_TEST_SECRET", "must-not-reach-the-child")
    config = ServerConfig(transport="stdio", command=sys.executable, args=[str(FIXTURE)], timeout_seconds=10)
    async def scenario():
        client = HospitalClient()
        assert {"get_patient", "get_results"} <= set(await client.health(config))
        patient = await client.call(config, "get_patient", {"patient_id": "SYNTHETIC-1"})
        lab = await client.call(config, "get_results", {"patient_id": "SYNTHETIC-1"})
        assert patient["source"] == "synthetic-ehr"
        assert lab["source"] == "synthetic-lab"
    asyncio.run(scenario())


@pytest.fixture
def http_peer():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(FIXTURE), "--http-port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env={key: value for key, value in os.environ.items() if key != "PRIVATE_BACKEND_TEST_SECRET"})
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Synthetic HTTP MCP peer exited before startup")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("Synthetic HTTP MCP peer did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_real_http_transport_uses_server_side_bearer_token(http_peer, monkeypatch):
    monkeypatch.setenv("MEDILINK_TEST_MCP_TOKEN", "synthetic-test-token")
    config = ServerConfig(transport="streamable_http", url=http_peer, bearer_token_env="MEDILINK_TEST_MCP_TOKEN", timeout_seconds=5)
    async def scenario():
        client = HospitalClient()
        assert "get_patient" in await client.health(config)
        assert (await client.call(config, "get_results", {"patient_id": "SYNTHETIC-1"}))["source"] == "synthetic-lab"
    asyncio.run(scenario())
    monkeypatch.setenv("MEDILINK_TEST_MCP_TOKEN", "wrong-token")
    with pytest.raises(IntegrationError):
        asyncio.run(HospitalClient().health(config))
