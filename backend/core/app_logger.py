# backend/core/app_logger.py

import logging
import logging.handlers
from pathlib import Path
from core.config import settings

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "governai.log"

_configured = False


def configure_logging() -> None:
    """
    Configures the root 'governai' logger once, with both a console
    handler (for immediate dev feedback) and a rotating file handler
    (for history). Safe to call multiple times - subsequent calls are
    no-ops, so modules can call this defensively without worrying
    about duplicate handlers.

    This is separate from DecisionLogger: this is for operational
    visibility (errors, warnings, startup info), not for the
    governance audit trail.
    """
    global _configured
    if _configured:
        return

    _LOG_DIR.mkdir(exist_ok=True)

    root_logger = logging.getLogger("governai")
    root_logger.setLevel(settings.log_level.upper())

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger under the 'governai' namespace, e.g.
    get_logger('agents.security') -> logger name 'governai.agents.security'.
    Ensures configure_logging() has run first.
    """
    configure_logging()
    return logging.getLogger(f"governai.{name}")