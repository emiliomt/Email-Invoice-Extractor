"""
Background run state for the web dashboard.

A single global RunState holds the current run's status, live log lines,
and final stats.  start_run() launches the extraction in a daemon thread
and captures all log output into the state's log buffer so the browser
can poll for it.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class _LogCapture(logging.Handler):
    """Appends formatted log records to a shared deque."""

    def __init__(self, target: deque) -> None:
        super().__init__()
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._target.append({"level": record.levelname, "msg": self.format(record)})
        except Exception:
            self.handleError(record)


class RunState:
    def __init__(self) -> None:
        self.running: bool = False
        self.logs: deque = deque(maxlen=2000)
        self.stats: Dict[str, Any] = {}
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self._lock = threading.Lock()

    def snapshot_logs(self, since: int = 0) -> Dict[str, Any]:
        all_logs: List[Dict] = list(self.logs)
        return {"logs": all_logs[since:], "total": len(all_logs)}


_state = RunState()


def get_state() -> RunState:
    return _state


def start_run(folder: str, dry_run: bool) -> bool:
    """
    Launch an extraction run in a background thread.
    Returns False if a run is already in progress.
    """
    with _state._lock:
        if _state.running:
            return False
        _state.running = True
        _state.logs.clear()
        _state.stats = {}
        _state.started_at = datetime.now(timezone.utc).isoformat()
        _state.finished_at = None

    thread = threading.Thread(target=_run, args=(folder, dry_run), daemon=True)
    thread.start()
    return True


def _run(folder: str, dry_run: bool) -> None:
    handler = _LogCapture(_state.logs)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        from src.extractor import run_extraction
        stats = run_extraction(folder=folder, dry_run=dry_run)
        _state.stats = stats
    except Exception as exc:
        logging.getLogger("runner").error("Run failed with unhandled exception: %s", exc)
        _state.stats = {"errors": 1}
    finally:
        root_logger.removeHandler(handler)
        _state.running = False
        _state.finished_at = datetime.now(timezone.utc).isoformat()
