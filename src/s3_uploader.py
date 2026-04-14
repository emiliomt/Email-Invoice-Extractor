import hashlib
import io
import logging
import zipfile
from datetime import datetime, timezone
from typing import List, Tuple

import boto3
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .pdf_extractor import PDFAttachment

logger = logging.getLogger(__name__)


class S3Uploader:
    """
    Uploads batches of PDFAttachment objects to S3 as ZIP archives.

    Each ZIP contains up to 50 PDFs.  S3 key format:
        {prefix}/batch_{YYYYMMDDTHHMMSS}_{batch_number:04d}.zip

    Inside the ZIP each PDF is stored as:
        {sha256(message_id)[:12]}_{safe_filename}
    so filenames are unique even when multiple emails carry identically
    named attachments.
    """

    BATCH_SIZE = 50

    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.bucket = settings.s3_bucket_name
        self.prefix = settings.s3_key_prefix.rstrip("/")
        # Timestamp fixed at construction so all batches in one run share it
        self._run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    def _zip_key(self, batch_number: int) -> str:
        return f"{self.prefix}/batch_{self._run_ts}_{batch_number:04d}.zip"

    @staticmethod
    def _arcname(attachment: PDFAttachment, message_id: str) -> str:
        """Unique filename to use inside the ZIP archive."""
        id_hash = hashlib.sha256(message_id.encode()).hexdigest()[:12]
        safe_name = (
            attachment.filename
            .replace("/", "_")
            .replace("\\", "_")
            .replace("\x00", "")
        )
        return f"{id_hash}_{safe_name}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def upload_zip_batch(
        self,
        items: List[Tuple[PDFAttachment, str]],
        batch_number: int,
        dry_run: bool = False,
    ) -> str:
        """
        Zip up to BATCH_SIZE (50) PDFs in memory and upload the archive to S3.

        items   – list of (PDFAttachment, message_id) pairs
        Returns the S3 key of the uploaded ZIP.
        """
        key = self._zip_key(batch_number)

        if dry_run:
            logger.info(
                "[DRY RUN] Would upload batch %d (%d PDFs) → s3://%s/%s",
                batch_number,
                len(items),
                self.bucket,
                key,
            )
            return key

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for att, message_id in items:
                arcname = self._arcname(att, message_id)
                zf.writestr(arcname, att.content)

        zip_bytes = buf.getvalue()
        logger.info(
            "Uploading batch %d (%d PDFs, %d bytes) → s3://%s/%s",
            batch_number,
            len(items),
            len(zip_bytes),
            self.bucket,
            key,
        )
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=zip_bytes,
            ContentType="application/zip",
            Metadata={
                "pdf-count": str(len(items)),
                "batch-number": str(batch_number),
                "upload-timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        return key

    def key_exists(self, key: str) -> bool:
        """Return True if the S3 key already exists."""
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise
