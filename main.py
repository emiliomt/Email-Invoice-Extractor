#!/usr/bin/env python3
"""
Email Invoice PDF Extractor

Scans a Neo.com mailbox via IMAP, extracts PDF attachments, and uploads them
to AWS S3 in batches of 50 PDFs per ZIP archive.  Processed email Message-IDs
are tracked so no file is ever uploaded twice.

Usage:
    python main.py [options]

Options:
    --folder FOLDER     IMAP folder to scan (default: INBOX)
    --dry-run           Log what would be uploaded without writing anything
    --list-folders      Print all available IMAP folders and exit
    --reset-state       Delete the state file to reprocess all emails
    --log-level LEVEL   DEBUG | INFO | WARNING | ERROR (default from .env)

Configuration:
    Copy .env.example to .env and fill in your Neo.com IMAP credentials
    and AWS S3 details before running.
"""

import argparse
import logging
import pathlib
import sys
from typing import Dict, List, Tuple

from src.config import settings
from src.imap_client import IMAPEmailClient
from src.pdf_extractor import PDFAttachment, extract_pdf_attachments
from src.s3_uploader import S3Uploader
from src.state_tracker import StateTracker


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract invoice PDFs from Neo.com email and upload to S3 as ZIP batches",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--folder",
        default="INBOX",
        metavar="FOLDER",
        help="IMAP folder to scan (default: INBOX)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and log what would be uploaded; do not write to S3 or update state",
    )
    parser.add_argument(
        "--list-folders",
        action="store_true",
        help="List all IMAP folders on the server and exit",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete the processed-IDs state file so all emails are reprocessed",
    )
    parser.add_argument(
        "--log-level",
        default=settings.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"Logging verbosity (default: {settings.log_level})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    logger = logging.getLogger("main")

    if args.reset_state:
        p = pathlib.Path(settings.state_file_path)
        if p.exists():
            p.unlink()
            logger.info("State file deleted — all emails will be reprocessed on next run")
        else:
            logger.info("No state file found at %s", p)
        return 0

    tracker = StateTracker(settings.state_file_path)
    uploader = S3Uploader()

    # ------------------------------------------------------------------ #
    # Phase 1: collect PDFs from all unprocessed emails                   #
    # ------------------------------------------------------------------ #
    # Each entry: (PDFAttachment, message_id)
    pdf_queue: List[Tuple[PDFAttachment, str]] = []
    # How many PDFs were queued per message (to know when a message is fully uploaded)
    message_pdf_counts: Dict[str, int] = {}

    stats = {"seen": 0, "skipped": 0, "queued": 0, "batches": 0, "uploaded": 0, "errors": 0}

    with IMAPEmailClient() as imap:
        if args.list_folders:
            for folder in sorted(imap.list_folders()):
                print(folder)
            return 0

        imap.select_folder(args.folder)

        for uid, message_id, msg in imap.iter_messages():
            stats["seen"] += 1

            if tracker.is_processed(message_id):
                logger.debug("Skipping already-processed message %s", message_id)
                stats["skipped"] += 1
                continue

            try:
                attachments = extract_pdf_attachments(msg)

                if not attachments:
                    logger.debug("No PDF attachments in message %s", message_id)
                    if not args.dry_run:
                        tracker.mark_processed(message_id)
                    continue

                for att in attachments:
                    pdf_queue.append((att, message_id))
                    stats["queued"] += 1

                message_pdf_counts[message_id] = len(attachments)

            except Exception as exc:
                logger.error(
                    "Error processing UID %d (%s): %s", uid, message_id, exc
                )
                stats["errors"] += 1

    logger.info(
        "Phase 1 complete — seen=%d  skipped=%d  queued=%d PDFs in %d new messages",
        stats["seen"],
        stats["skipped"],
        stats["queued"],
        len(message_pdf_counts),
    )

    if not pdf_queue:
        logger.info("Nothing to upload.")
        return 0

    # ------------------------------------------------------------------ #
    # Phase 2: upload in batches of 50 as separate ZIP archives           #
    # ------------------------------------------------------------------ #
    batch_size = S3Uploader.BATCH_SIZE
    # Track how many PDFs from each message have been successfully uploaded
    uploaded_counts: Dict[str, int] = {}

    for batch_number, start in enumerate(range(0, len(pdf_queue), batch_size), start=1):
        batch = pdf_queue[start : start + batch_size]
        try:
            key = uploader.upload_zip_batch(batch, batch_number, dry_run=args.dry_run)
            stats["batches"] += 1
            stats["uploaded"] += len(batch)
            logger.info(
                "Batch %d uploaded (%d PDFs) → s3://%s/%s",
                batch_number,
                len(batch),
                uploader.bucket,
                key,
            )

            if not args.dry_run:
                for _att, message_id in batch:
                    uploaded_counts[message_id] = uploaded_counts.get(message_id, 0) + 1

                # Mark a message as processed only after ALL its PDFs have
                # been included in successfully uploaded ZIPs
                for message_id, total in message_pdf_counts.items():
                    if uploaded_counts.get(message_id, 0) >= total:
                        tracker.mark_processed(message_id)

        except Exception as exc:
            logger.error("Failed to upload batch %d: %s", batch_number, exc)
            stats["errors"] += len(batch)

    logger.info(
        "Run complete — seen=%d  skipped=%d  queued=%d  batches=%d  uploaded=%d  errors=%d",
        stats["seen"],
        stats["skipped"],
        stats["queued"],
        stats["batches"],
        stats["uploaded"],
        stats["errors"],
    )
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
