"""Quiet, bounded Docker readiness probe; never prints errors or credentials."""

import http.client
import json


def check(port=8000, timeout=3):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/api/health/ready")
        response = conn.getresponse()
        content = response.read(4097)
        if response.status != 200 or len(content) > 4096:
            return False
        body = json.loads(content)
        return body.get("data", {}).get("status") == "ready"
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(0 if check() else 1)
