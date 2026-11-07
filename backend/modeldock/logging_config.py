import json
import logging
import time


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for name in ("request_id", "task_id", "model_id", "status", "duration_ms", "method", "path"):
            if hasattr(record, name):
                entry[name] = getattr(record, name)
        if record.exc_info:
            # Tracebacks can include user payloads. Log exception class only; correlate with request/task id.
            entry["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "unknown"
        return json.dumps(entry, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
