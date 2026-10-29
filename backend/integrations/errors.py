from fastapi import HTTPException

ERRORS = {
    "APPROVAL_REQUIRED": (403, "A valid action approval is required"),
    "APPROVAL_STALE": (409, "Approval expired or its action evidence changed"),
    "ACTION_SIGNING_NOT_CONFIGURED": (503, "Approved-action signing is not configured"),
    "MCP_NOT_CONFIGURED": (503, "Hospital integration is not configured"),
    "MCP_CONFIG_INVALID": (503, "Hospital integration configuration is invalid"),
    "MCP_UNAVAILABLE": (503, "Hospital integration is unavailable"),
    "MCP_TIMEOUT": (504, "Hospital integration timed out"),
    "MCP_TOOL_UNAVAILABLE": (502, "Required hospital capability is unavailable"),
    "MCP_SCHEMA_INVALID": (502, "Hospital capability schema is missing or invalid"),
    "MCP_INPUT_INVALID": (422, "Arguments do not match the hospital capability schema"),
    "MCP_OUTPUT_INVALID": (502, "Hospital capability returned invalid data"),
    "MCP_REMOTE_ERROR": (502, "Hospital capability reported a failure"),
    "MCP_WRITE_BLOCKED": (403, "Write capability is outside the approved managed workflows"),
}


class IntegrationError(HTTPException):
    def __init__(self, code: str, *, dispatched=False):
        status, message = ERRORS[code]
        super().__init__(status, message)
        self.error_code = code
        self.dispatched = dispatched
