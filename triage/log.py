"""Logging setup: readable console output plus an optional per-run debug log file."""

import logging
import sys
from contextlib import contextmanager
from pathlib import Path

CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)-16s %(message)s"
FILE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)-16s [%(threadName)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"

_NOISY = ("httpx", "httpcore", "openai", "langchain", "langgraph", "urllib3")


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT, DATE_FORMAT))
    # Library chatter (HTTP clients etc.) only reaches the console when it is a warning or worse.
    console.addFilter(lambda r: r.name.startswith("triage") or r.levelno >= logging.WARNING)
    root.addHandler(console)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


@contextmanager
def log_to_file(path: Path):
    """Capture everything (DEBUG and up) into `path` while the block runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()


def banner(log: logging.Logger, text: str) -> None:
    line = "═" * 78
    log.info(line)
    log.info(text)
    log.info(line)


def section(log: logging.Logger, text: str) -> None:
    log.info(f"── {text} " + "─" * max(0, 74 - len(text)))
