from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_configured = False


def configure_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    global _configured
    root = logging.getLogger("landcad")
    if _configured:
        return root

    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "landcad.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    root.propagate = False
    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"landcad.{name}")
