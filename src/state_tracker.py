import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Set, Union

if TYPE_CHECKING:
    from .config import Settings

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


class S3StateTracker:
    """
    Same interface as StateTracker but persists the processed-ID set as a
    JSON object in S3.  Use this on stateless hosts (Render free tier,
    Lambda) where the local filesystem is ephemeral.

    Configure via STATE_S3_KEY, e.g. "state/processed_emails.json".
    """

    def __init__(self, bucket: str, key: str, s3_client) -> None:
        self._bucket = bucket
        self._key = key
        self._s3 = s3_client
        self._processed: Set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._key)
            data = json.loads(obj["Body"].read())
            self._processed = set(data.get("processed_ids", []))
            logger.info(
                "Loaded %d processed IDs from s3://%s/%s",
                len(self._processed), self._bucket, self._key,
            )
        except self._s3.exceptions.NoSuchKey:
            logger.info("No state object at s3://%s/%s — starting fresh", self._bucket, self._key)
        except Exception as exc:
            logger.error("Could not load S3 state: %s — starting fresh", exc)
            self._processed = set()

    def _save(self) -> None:
        body = json.dumps({"processed_ids": sorted(self._processed)}, indent=2)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._key,
            Body=body.encode(),
            ContentType="application/json",
        )
        logger.debug("S3 state persisted (%d IDs)", len(self._processed))

    def is_processed(self, message_id: str) -> bool:
        return message_id in self._processed

    def mark_processed(self, message_id: str) -> None:
        self._processed.add(message_id)
        self._save()

    def count(self) -> int:
        return len(self._processed)


def create_tracker(settings: "Settings") -> Union[StateTracker, S3StateTracker]:
    """Return the right tracker based on configuration."""
    if settings.state_s3_key:
        import boto3
        client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            endpoint_url=settings.aws_endpoint_url or None,
        )
        return S3StateTracker(settings.s3_bucket_name, settings.state_s3_key, client)
    return StateTracker(settings.state_file_path)
