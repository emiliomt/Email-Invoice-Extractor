import logging
from typing import Dict

logger = logging.getLogger(__name__)


def run_extraction(
    folder: str = "INBOX", dry_run: bool = False, file_type: str = "pdf"
) -> Dict[str, int]:
    """
    Run the full attachment extraction pipeline.

    Phase 1 — IMAP: scan all unprocessed emails, collect attachments.
    Phase 2 — S3:   upload files in batches of 50 as ZIP archives.

    file_type: "pdf", "xml", or "both" — controls which attachment types are extracted.

    Returns a stats dict with keys:
        seen, skipped, queued, batches, uploaded, errors
    """
    from .config import settings
    from .imap_client import IMAPEmailClient
    from .pdf_extractor import extract_attachments
    from .s3_uploader import S3Uploader
    from .state_tracker import create_tracker

    tracker = create_tracker(settings)
    uploader = S3Uploader()

    attachment_queue = []          # list of (Attachment, message_id)
    message_att_counts = {}        # message_id -> number of attachments queued
    stats = {"seen": 0, "skipped": 0, "queued": 0, "batches": 0, "uploaded": 0, "errors": 0}
    _no_att_samples_logged = 0     # log MIME structure for first 20 empty emails

    logger.info("Extracting file type: %s", file_type)

    # ------------------------------------------------------------------ #
    # Phase 1: collect attachments from all unprocessed emails             #
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
                attachments = extract_attachments(msg, file_type=file_type)

                if not attachments:
                    if _no_att_samples_logged < 20:
                        _no_att_samples_logged += 1
                        parts_info = []
                        for p in msg.walk():
                            if p.get_content_maintype() == "multipart":
                                continue
                            fn = p.get_filename() or "-"
                            parts_info.append(f"{p.get_content_type()}[fn={fn}]")
                        logger.info(
                            "DIAG no-attachment #%d uid=%d parts: %s",
                            _no_att_samples_logged, uid,
                            " | ".join(parts_info) or "(empty)",
                        )
                    else:
                        logger.debug("No %s attachments in %s", file_type, message_id)
                    if not dry_run:
                        tracker.mark_processed(message_id)
                    continue

                for att in attachments:
                    attachment_queue.append((att, message_id))
                    stats["queued"] += 1

                message_att_counts[message_id] = len(attachments)

            except Exception as exc:
                logger.error("Error processing UID %d (%s): %s", uid, message_id, exc)
                stats["errors"] += 1

    logger.info(
        "Phase 1 complete — seen=%d  skipped=%d  queued=%d files from %d messages",
        stats["seen"], stats["skipped"], stats["queued"], len(message_att_counts),
    )

    if not attachment_queue:
        logger.info("Nothing to upload.")
        return stats

    # ------------------------------------------------------------------ #
    # Phase 2: upload in batches of 50 as ZIP archives                    #
    # ------------------------------------------------------------------ #
    batch_size = S3Uploader.BATCH_SIZE
    uploaded_counts: Dict[str, int] = {}

    for batch_number, start in enumerate(range(0, len(attachment_queue), batch_size), start=1):
        batch = attachment_queue[start : start + batch_size]
        try:
            key = uploader.upload_zip_batch(batch, batch_number, dry_run=dry_run)
            stats["batches"] += 1
            stats["uploaded"] += len(batch)
            logger.info(
                "Batch %d uploaded (%d files) → s3://%s/%s",
                batch_number, len(batch), uploader.bucket, key,
            )

            if not dry_run:
                for _att, message_id in batch:
                    uploaded_counts[message_id] = uploaded_counts.get(message_id, 0) + 1
                for message_id, total in message_att_counts.items():
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
