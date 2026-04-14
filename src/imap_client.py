import email
import logging
from email.message import Message
from typing import Iterator, Optional, Tuple

from imapclient import IMAPClient
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import settings

logger = logging.getLogger(__name__)


class IMAPEmailClient:
    """
    Connects to a Neo.com IMAP server and yields parsed email messages.

    Uses UID-based fetching (stable across sessions) and opens folders in
    read-only mode so message \\Seen flags are never altered.
    """

    def __init__(self) -> None:
        self._client: Optional[IMAPClient] = None

    def connect(self) -> None:
        logger.info(
            "Connecting to IMAP %s:%d as %s",
            settings.imap_host,
            settings.imap_port,
            settings.imap_username,
        )
        self._client = IMAPClient(
            host=settings.imap_host,
            port=settings.imap_port,
            ssl=True,
            use_uid=True,
        )
        self._client.login(settings.imap_username, settings.imap_password)
        logger.info("IMAP login successful")

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "IMAPEmailClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    def list_folders(self) -> list[str]:
        """Return all folder names on the server."""
        return [f[2] for f in self._client.list_folders()]

    def select_folder(self, folder: str = "INBOX") -> int:
        """
        Select a mailbox folder in read-only mode.
        Returns the number of messages in the folder.
        """
        info = self._client.select_folder(folder, readonly=True)
        count = info[b"EXISTS"]
        logger.info("Folder '%s' selected — %d messages", folder, count)
        return count

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _search_all_uids(self) -> list[int]:
        uids = self._client.search(["ALL"])
        logger.debug("SEARCH ALL returned %d UIDs", len(uids))
        return uids

    def _fetch_message(self, uid: int) -> Tuple[str, Message]:
        """
        Fetch a single message by UID.
        Returns (message_id, parsed Message).

        If the email has no Message-ID header a synthetic ID is generated
        so deduplication still works.
        """
        raw = self._client.fetch([uid], ["RFC822"])
        if uid not in raw:
            raise KeyError(f"UID {uid} not found in fetch response")

        parsed: Message = email.message_from_bytes(raw[uid][b"RFC822"])
        message_id = parsed.get("Message-ID", "").strip()
        if not message_id:
            message_id = f"<uid-{uid}@synthetic>"
            logger.warning(
                "UID %d has no Message-ID header — using synthetic ID %s",
                uid,
                message_id,
            )
        return message_id, parsed

    def iter_messages(
        self, batch_size: int = 50
    ) -> Iterator[Tuple[int, str, Message]]:
        """
        Yield (uid, message_id, parsed_message) for every message in the
        currently selected folder, fetched in batches to keep memory bounded.
        """
        all_uids = self._search_all_uids()
        logger.info(
            "Iterating %d UIDs in batches of %d", len(all_uids), batch_size
        )
        for i in range(0, len(all_uids), batch_size):
            batch = all_uids[i : i + batch_size]
            logger.debug("Processing batch %d–%d", i + 1, i + len(batch))
            for uid in batch:
                try:
                    message_id, msg = self._fetch_message(uid)
                    yield uid, message_id, msg
                except Exception as exc:
                    logger.error("Failed to fetch UID %d: %s", uid, exc)
