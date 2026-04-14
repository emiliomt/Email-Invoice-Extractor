import logging
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import List

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
# The PDF spec allows the %PDF header within the first 1024 bytes.
_PDF_MAGIC_WINDOW = 1024

# These MIME main-types can never produce a valid PDF payload.
_SKIP_MAINTYPE = frozenset({"text", "image", "audio", "video", "multipart", "message"})


@dataclass
class PDFAttachment:
    filename: str
    content: bytes
    content_type: str
    size_bytes: int


def extract_pdf_attachments(msg: Message) -> List[PDFAttachment]:
    """
    Walk all MIME parts of an email and return any PDF attachments found.

    Detection strategy (in order):
      1. Skip obvious non-PDF main types (text, image, audio, video, …).
      2. Decode the payload of every remaining part.
      3. Accept it as a PDF if and only if the %PDF magic signature appears
         within the first 1024 bytes (PDF spec requirement).

    This approach catches PDFs regardless of Content-Type or filename,
    including application/octet-stream, application/download,
    application/force-download, binary/octet-stream, and even parts with
    no filename at all.  The magic-bytes check is the authoritative guard.
    """
    attachments: List[PDFAttachment] = []
    part_summaries: List[str] = []

    for part in msg.walk():
        ct = part.get_content_type()
        maintype = part.get_content_maintype()

        if maintype == "multipart":
            continue

        if maintype in _SKIP_MAINTYPE:
            part_summaries.append(ct)
            continue

        att = _try_extract_pdf(part, part_summaries)
        if att:
            attachments.append(att)

    if not attachments and logger.isEnabledFor(logging.DEBUG):
        logger.debug("No PDFs found — MIME parts: %s", " | ".join(part_summaries) or "(none)")

    return attachments


def _try_extract_pdf(
    part: Message,
    summaries: List[str],
) -> "PDFAttachment | None":
    """
    Attempt to extract a PDF from a single MIME part.
    Appends a human-readable summary entry to `summaries` for diagnostics.
    Returns a PDFAttachment on success, or None if the part is not a PDF.
    """
    content_type = part.get_content_type().lower()
    filename = _decode_filename(part)
    encoding = part.get("Content-Transfer-Encoding", "-")

    # Decode transfer encoding (base64, quoted-printable, …)
    payload = part.get_payload(decode=True)
    if not payload:
        summaries.append(f"{content_type}[fn={filename or '-'},enc={encoding},EMPTY]")
        return None

    first_hex = payload[:8].hex()
    has_magic = _PDF_MAGIC in payload[:_PDF_MAGIC_WINDOW]

    summaries.append(
        f"{content_type}[fn={filename or '-'},enc={encoding},"
        f"sz={len(payload)},magic={'YES' if has_magic else first_hex}]"
    )

    if not has_magic:
        if "pdf" in content_type or (filename and filename.lower().endswith(".pdf")):
            logger.warning(
                "Part declared as PDF but %%PDF magic not found in first %d bytes "
                "(content_type=%s, size=%d, first_bytes=%s) — skipping",
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
