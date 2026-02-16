"""Structured JSON logging for FollowFlow."""

import logging
import json
import sys
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = self.formatException(record.exc_info)
        # Include any extra fields passed via `extra={}`
        for key in ("action", "username", "batch_id", "status", "detail"):
            if hasattr(record, key):
                log_data[key] = getattr(record, key)
        return json.dumps(log_data)


def get_logger(name: str = "followflow") -> logging.Logger:
    """Return a logger configured with JSON output.

    Args:
        name: Logger name, typically the module name.

    Returns:
        A configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    return logger
