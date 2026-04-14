import json
import logging
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)


class StateTracker:
    """
    Persists processed email Message-IDs to a JSON file to prevent
    reprocessing emails across runs.

    Storage format:
        {"processed_ids": ["<id1@host>", "<id2@host>", ...]}

    Writes are atomic: data is written to a .tmp file then renamed,
    so a mid-write kill cannot corrupt the state file.
    """

    def __init__(self, state_file: str) -> None:
        self.state_file = Path(state_file)
        self._processed: Set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.state_file.exists():
            logger.info("No state file at %s — starting fresh", self.state_file)
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._processed = set(data.get("processed_ids", []))
            logger.info(
                "Loaded %d processed message IDs from %s",
                len(self._processed),
                self.state_file,
            )
        except (json.JSONDecodeError, IOError) as exc:
            logger.error(
                "Could not load state file %s: %s — starting fresh", self.state_file, exc
            )
            self._processed = set()

    def _save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"processed_ids": sorted(self._processed)}, fh, indent=2)
        tmp.replace(self.state_file)  # atomic on POSIX
        logger.debug("State persisted (%d IDs)", len(self._processed))

    def is_processed(self, message_id: str) -> bool:
        return message_id in self._processed

    def mark_processed(self, message_id: str) -> None:
        self._processed.add(message_id)
        self._save()

    def count(self) -> int:
        return len(self._processed)
