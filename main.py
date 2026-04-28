#!/usr/bin/env python3
"""
Email Invoice PDF Extractor — CLI entry point.

For a web dashboard run:  uvicorn web.app:app --reload

Usage:
    python main.py [options]

Options:
    --folder FOLDER     IMAP folder to scan (default: INBOX)
    --dry-run           Log what would be uploaded without writing anything
    --list-folders      Print all available IMAP folders and exit
    --reset-state       Delete the state file to reprocess all emails
    --log-level LEVEL   DEBUG | INFO | WARNING | ERROR (default from .env)
"""

import argparse
import logging
import pathlib
import sys

from src.config import settings


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
    )
    parser.add_argument("--folder", default="INBOX", help="IMAP folder to scan (default: INBOX)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and log without uploading or updating state")
    parser.add_argument("--list-folders", action="store_true",
                        help="List all IMAP folders and exit")
    parser.add_argument("--reset-state", action="store_true",
                        help="Delete the state file to reprocess all emails")
    parser.add_argument("--file-type", default=settings.file_type,
                        choices=["pdf", "xml", "both"],
                        help="Attachment type to extract: pdf, xml, or both (default: pdf)")
    parser.add_argument("--log-level", default=settings.log_level,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
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

    if args.list_folders:
        from src.imap_client import IMAPEmailClient
        with IMAPEmailClient() as imap:
            for folder in sorted(imap.list_folders()):
                print(folder)
        return 0

    from src.extractor import run_extraction
    stats = run_extraction(folder=args.folder, dry_run=args.dry_run, file_type=args.file_type)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
