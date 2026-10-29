"""Stable backend handoff errors; adapters use codes rather than provider text."""

from fastapi import HTTPException

ERRORS = {
    "AI_UNAVAILABLE": 503,
    "AI_TIMEOUT": 504,
    "AI_OUTPUT_INVALID": 502,
    "DATABASE_UNAVAILABLE": 503,
    "DATABASE_TIMEOUT": 504,
    "DATABASE_CONFLICT": 409,
    "RECOVERY_NOT_CONFIGURED": 503,
    "RECOVERY_UNAVAILABLE": 503,
    "RECOVERY_TIMEOUT": 504,
    "RECOVERY_OUTPUT_INVALID": 502,
    "RECOVERY_NOT_READY": 409,
}


class BackendError(HTTPException):
    def __init__(self, code):
        super().__init__(ERRORS[code], "Operation could not be completed safely.")
        self.error_code = code
