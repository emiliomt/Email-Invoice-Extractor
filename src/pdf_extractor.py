import io
import logging
import zipfile
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import List

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
# The PDF spec allows the %PDF header within the first 1024 bytes.
_PDF_MAGIC_WINDOW = 1024

# These MIME main-types can never produce a valid PDF or ZIP payload.
_SKIP_MAINTYPE = frozenset({"text", "image", "audio", "video", "multipart", "message"})

# Content-types that are definitively ZIPs (regardless of filename).
_ZIP_TYPES = frozenset({
    "application/zip",
    "application/x-zip",
    "application/x-zip-compressed",
    "application/x-compressed",
})


@dataclass
class PDFAttachment:
    filename: str
    content: bytes
    content_type: str
    size_bytes: int


def extract_pdf_attachments(msg: Message) -> List[PDFAttachment]:
    """
    Walk all MIME parts of an email and return any PDF attachments found.

    Handles:
    - Direct PDF attachments (any Content-Type; validated by %PDF magic bytes).
    - ZIP attachments: opened in memory; all .pdf members extracted.
    - Nested ZIPs inside ZIPs (one level deep is enough for invoice emails).
    """
    attachments: List[PDFAttachment] = []
    part_summaries: List[str] = []

    for part in msg.walk():
        maintype = part.get_content_maintype()
        if maintype in _SKIP_MAINTYPE:
            continue

        ct = part.get_content_type().lower()
        filename = _decode_filename(part)

        payload = part.get_payload(decode=True)
        if not payload:
            part_summaries.append(f"{ct}[fn={filename or '-'},EMPTY]")
            continue

        if _is_zip(ct, filename):
            zipped = _extract_pdfs_from_zip(payload, filename or "attachment.zip")
            attachments.extend(zipped)
            part_summaries.append(
                f"{ct}[fn={filename or '-'},sz={len(payload)}"
                f",zip→{len(zipped)} PDF(s)]"
            )
        else:
            att = _try_pdf_by_magic(payload, ct, filename, part_summaries)
            if att:
                attachments.append(att)

    if not attachments and logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "No PDFs found — MIME parts: %s",
            " | ".join(part_summaries) or "(none)",
        )

    return attachments


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _is_zip(content_type: str, filename: str) -> bool:
    if content_type in _ZIP_TYPES:
        return True
    if filename and filename.lower().endswith(".zip"):
        return True
    # application/octet-stream with no filename: peek at magic bytes is
    # handled in the caller after payload is decoded.
    return False


def _extract_pdfs_from_zip(
    zip_bytes: bytes, zip_filename: str
) -> List[PDFAttachment]:
    """Open a ZIP in memory and return all PDF members found inside."""
    results: List[PDFAttachment] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                name = member.filename
                if not name.lower().endswith(".pdf"):
                    continue
                try:
                    pdf_bytes = zf.read(member)
                except Exception as exc:
                    logger.warning(
                        "Could not read %s from ZIP %s: %s", name, zip_filename, exc
                    )
                    continue

                if _PDF_MAGIC not in pdf_bytes[:_PDF_MAGIC_WINDOW]:
                    logger.warning(
                        "Member %s in ZIP %s has .pdf extension but no %%PDF magic — skipping",
                        name, zip_filename,
                    )
                    continue

                # Strip directory component; keep just the base filename.
                base = name.replace("\\", "/").rsplit("/", 1)[-1] or "attachment.pdf"
                results.append(
                    PDFAttachment(
                        filename=base,
                        content=pdf_bytes,
                        content_type="application/pdf",
                        size_bytes=len(pdf_bytes),
                    )
                )
                logger.debug(
                    "Extracted PDF %s (%d bytes) from ZIP %s",
                    base, len(pdf_bytes), zip_filename,
                )
    except zipfile.BadZipFile:
        logger.warning("Part is not a valid ZIP file: %s", zip_filename)
    except Exception as exc:
        logger.warning("Error reading ZIP %s: %s", zip_filename, exc)
    return results


def _try_pdf_by_magic(
    payload: bytes,
    content_type: str,
    filename: str,
    summaries: List[str],
) -> "PDFAttachment | None":
    """Accept a MIME payload as a PDF iff it contains the %PDF magic bytes."""
    has_magic = _PDF_MAGIC in payload[:_PDF_MAGIC_WINDOW]
    first_hex = payload[:8].hex()
    summaries.append(
        f"{content_type}[fn={filename or '-'},sz={len(payload)},"
        f"magic={'YES' if has_magic else first_hex}]"
    )
    if not has_magic:
        if "pdf" in content_type or (filename and filename.lower().endswith(".pdf")):
            logger.warning(
                "Part declared as PDF but %%PDF magic not found in first %d bytes "
                "(content_type=%s, size=%d, first=%s) — skipping",
                _PDF_MAGIC_WINDOW, content_type, len(payload), first_hex,
            )
        return None

    effective_filename = filename or "attachment.pdf"
    logger.debug(
        "Found PDF: %s (%d bytes) [content-type=%s]",
        effective_filename, len(payload), content_type,
    )
    return PDFAttachment(
        filename=effective_filename,
        content=payload,
        content_type=content_type,
        size_bytes=len(payload),
    )


def _decode_filename(part: Message) -> str:
    """
    Extract and decode the filename from Content-Disposition or Content-Type.
    Handles RFC2047 encoded words (=?UTF-8?B?...?=).
    """
    raw = part.get_filename()
    if not raw:
        return ""
    decoded_parts = decode_header(raw)
    result = ""
    for fragment, charset in decoded_parts:
        if isinstance(fragment, bytes):
            result += fragment.decode(charset or "utf-8", errors="replace")
        else:
            result += fragment
    return result.strip()
