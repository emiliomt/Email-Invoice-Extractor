import logging
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import List

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"


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
    - multipart/mixed with application/pdf parts
    - application/octet-stream parts with a .pdf filename
    - Inline PDFs (Content-Disposition: inline)
    - Base64 / quoted-printable encoded parts (decoded by the email library)
    - RFC2047-encoded filenames
    """
    attachments: List[PDFAttachment] = []

    if not msg.is_multipart():
        if _is_pdf_part(msg):
            att = _build_attachment(msg)
            if att:
                attachments.append(att)
        return attachments

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if _is_pdf_part(part):
            att = _build_attachment(part)
            if att:
                attachments.append(att)
                logger.debug(
                    "Found PDF: %s (%d bytes)", att.filename, att.size_bytes
                )

    return attachments


def _is_pdf_part(part: Message) -> bool:
    content_type = part.get_content_type().lower()
    if content_type == "application/pdf":
        return True
    # Some mailers send PDFs as application/octet-stream
    if content_type == "application/octet-stream":
        filename = _decode_filename(part)
        if filename and filename.lower().endswith(".pdf"):
            return True
    return False


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


def _build_attachment(part: Message) -> PDFAttachment | None:
    """
    Decode a MIME part and return a PDFAttachment.
    Returns None if the payload is empty or does not start with the PDF
    magic bytes (%PDF), which guards against mislabelled parts.
    """
    payload = part.get_payload(decode=True)  # decode=True handles base64/qp
    if not payload:
        logger.warning("PDF part has empty payload — skipping")
        return None

    if not payload.startswith(_PDF_MAGIC):
        logger.warning(
            "Part claimed to be PDF but missing %%PDF magic bytes "
            "(content_type=%s, size=%d) — skipping",
            part.get_content_type(),
            len(payload),
        )
        return None

    filename = _decode_filename(part) or "attachment.pdf"

    return PDFAttachment(
        filename=filename,
        content=payload,
        content_type=part.get_content_type(),
        size_bytes=len(payload),
    )
