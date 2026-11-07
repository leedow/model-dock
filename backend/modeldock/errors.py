from typing import Any


class DockError(Exception):
    def __init__(
        self,
        message: str,
        code: str = "invalid_request",
        status: int = 400,
        param: str | None = None,
        error_type: str | None = None,
    ):
        error_type = error_type or ("server_error" if status >= 500 else "invalid_request_error")
        self.status = status
        self.error: dict[str, Any] = {"message": message, "type": error_type, "param": param, "code": code}
        super().__init__(message)
