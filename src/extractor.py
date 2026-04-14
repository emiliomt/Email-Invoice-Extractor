import logging
from typing import Dict

logger = logging.getLogger(__name__)


def run_extraction(folder: str = "INBOX", dry_run: bool = False) -> Dict[str, int]:
    """
    Run the full PDF extraction pipeline.

    Phase 1 — IMAP: scan all unprocessed emails, collect PDF attachments.
    Phase 2 — S3:   upload PDFs in batches of 50 as ZIP archives.

    Returns a stats dict with keys:
        seen, skipped, queued, batches, uploaded, errors
    """
    from .config import settings
    from .imap_client import IMAPEmailClient
    from .pdf_extractor import extract_pdf_attachments
    from .s3_uploader import S3Uploader
    from .state_tracker import create_tracker

    tracker = create_tracker(settings)
    uploader = S3Uploader()

    pdf_queue = []          # list of (PDFAttachment, message_id)
    message_pdf_counts = {} # message_id -> number of PDFs queued from that message
    stats = {"seen": 0, "skipped": 0, "queued": 0, "batches": 0, "uploaded": 0, "errors": 0}

    # ------------------------------------------------------------------ #
    # Phase 1: collect PDFs from all unprocessed emails                   #
    # ------------------------------------------------------------------ #
    with IMAPEmailClient() as imap:
        imap.select_folder(folder)

        for uid, message_id, msg in imap.iter_messages():
            stats["seen"] += 1

            if tracker.is_processed(message_id):
                logger.debug("Skipping already-processed %s", message_id)
                stats["skipped"] += 1
                continue

            try:
                attachments = extract_pdf_attachments(msg)

                if not attachments:
                    logger.debug("No PDF attachments in %s", message_id)
                    if not dry_run:
                        tracker.mark_processed(message_id)
                    continue

                for att in attachments:
                    pdf_queue.append((att, message_id))
                    stats["queued"] += 1

                message_pdf_counts[message_id] = len(attachments)

            except Exception as exc:
                logger.error("Error processing UID %d (%s): %s", uid, message_id, exc)
                stats["errors"] += 1

    logger.info(
        "Phase 1 complete — seen=%d  skipped=%d  queued=%d PDFs from %d messages",
        stats["seen"], stats["skipped"], stats["queued"], len(message_pdf_counts),
    )

    if not pdf_queue:
        logger.info("Nothing to upload.")
        return stats

    # ------------------------------------------------------------------ #
    # Phase 2: upload in batches of 50 as ZIP archives                    #
    # ------------------------------------------------------------------ #
    batch_size = S3Uploader.BATCH_SIZE
    uploaded_counts: Dict[str, int] = {}

    for batch_number, start in enumerate(range(0, len(pdf_queue), batch_size), start=1):
        batch = pdf_queue[start : start + batch_size]
        try:
            key = uploader.upload_zip_batch(batch, batch_number, dry_run=dry_run)
            stats["batches"] += 1
            stats["uploaded"] += len(batch)
            logger.info(
                "Batch %d uploaded (%d PDFs) → s3://%s/%s",
                batch_number, len(batch), uploader.bucket, key,
            )

            if not dry_run:
                for _att, message_id in batch:
                    uploaded_counts[message_id] = uploaded_counts.get(message_id, 0) + 1
                for message_id, total in message_pdf_counts.items():
                    if uploaded_counts.get(message_id, 0) >= total:
                        tracker.mark_processed(message_id)

        except Exception as exc:
            logger.error("Failed to upload batch %d: %s", batch_number, exc)
            stats["errors"] += len(batch)

    logger.info(
        "Run complete — seen=%d  skipped=%d  queued=%d  batches=%d  uploaded=%d  errors=%d",
        stats["seen"], stats["skipped"], stats["queued"],
        stats["batches"], stats["uploaded"], stats["errors"],
    )
    return stats
