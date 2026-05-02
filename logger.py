"""
logger.py — Structured logging for the fishing loop.

Uses Python's stdlib logging so output goes to both the console and a
rotating file (crimmyfish.log). The GUI can attach a handler that forwards
messages to a tkinter Text widget via a thread-safe queue.
"""

import logging
import os
import queue
from logging.handlers import RotatingFileHandler

LOG_PATH = os.path.join(os.path.dirname(__file__), "crimmyfish.log")

# Module-level queue lets the GUI drain messages without blocking the loop thread.
_gui_queue: queue.Queue = queue.Queue()

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def _build_logger() -> logging.Logger:
    log = logging.getLogger("crimmyfish")
    log.setLevel(logging.DEBUG)

    if log.handlers:
        return log  # Already configured (reload-safe)

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(_fmt)
    log.addHandler(ch)

    # Rotating file — max 2 MB, keep 3 backups
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_fmt)
    log.addHandler(fh)

    return log


log = _build_logger()


class _QueueHandler(logging.Handler):
    """Forwards records to the GUI queue."""

    def emit(self, record: logging.LogRecord) -> None:
        _gui_queue.put_nowait(self.format(record))


_qh = _QueueHandler()
_qh.setFormatter(_fmt)
log.addHandler(_qh)


def get_gui_queue() -> queue.Queue:
    """Return the queue that the GUI should drain for log messages."""
    return _gui_queue


def info(msg: str, *args) -> None:
    log.info(msg, *args)


def debug(msg: str, *args) -> None:
    log.debug(msg, *args)


def warning(msg: str, *args) -> None:
    log.warning(msg, *args)


def error(msg: str, *args) -> None:
    log.error(msg, *args)
