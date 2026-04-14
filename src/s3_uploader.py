import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .pdf_extractor import PDFAttachment

logger = logging.getLogger(__name__)


class S3Uploader:
    """
    Uploads PDFAttachment objects to S3.

    S3 key format:
        {prefix}/{YYYY}/{MM}/{sha256(message_id)[:12]}_{safe_filename}

    The 12-char hash of the Message-ID prevents collisions when two emails
    carry PDFs with identical filenames.  Date partitioning enables S3
    Lifecycle rules (e.g. transition to Glacier after 1 year).
    """

    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.bucket = settings.s3_bucket_name
        self.prefix = settings.s3_key_prefix.rstrip("/")

    def _build_key(
        self,
        attachment: PDFAttachment,
        message_id: str,
        email_date: str,
    ) -> str:
        id_hash = hashlib.sha256(message_id.encode()).hexdigest()[:12]

        try:
            dt = datetime.fromisoformat(email_date)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        year_month = dt.strftime("%Y/%m")

        # Sanitise filename: strip path separators and null bytes
        safe_name = (
            attachment.filename
            .replace("/", "_")
            .replace("\\", "_")
            .replace("\x00", "")
        )

        return f"{self.prefix}/{year_month}/{id_hash}_{safe_name}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def upload(
        self,
        attachment: PDFAttachment,
        message_id: str,
        email_date: str = "",
        dry_run: bool = False,
    ) -> str:
        """
        Upload a single PDF to S3 and return the S3 key.

        In dry_run mode the upload is skipped but the key is still returned
        so callers can log what would have been written.
        """
        key = self._build_key(attachment, message_id, email_date)

        if dry_run:
            logger.info(
                "[DRY RUN] Would upload %s (%d bytes) → s3://%s/%s",
                attachment.filename,
                attachment.size_bytes,
                self.bucket,
                key,
            )
            return key

        logger.info(
            "Uploading %s (%d bytes) → s3://%s/%s",
            attachment.filename,
            attachment.size_bytes,
            self.bucket,
            key,
        )
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=attachment.content,
            ContentType="application/pdf",
            Metadata={
                "original-filename": attachment.filename[:1024],
                "source-message-id": message_id[:256],
                "upload-timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        return key

    def key_exists(self, key: str) -> bool:
        """Return True if the S3 key already exists (idempotency check)."""
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise
