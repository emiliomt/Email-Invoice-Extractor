import io
import logging
import zipfile
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import List

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
_PDF_MAGIC_WINDOW = 1024

# MIME main-types that can never produce a valid file attachment.
_SKIP_MAINTYPE = frozenset({"image", "audio", "video", "multipart", "message"})

# Content-types that are definitively ZIPs (regardless of filename).
_ZIP_TYPES = frozenset({
    "application/zip",
    "application/x-zip",
    "application/x-zip-compressed",
    "application/x-compressed",
})

# Content-types that are definitively XML.
_XML_CONTENT_TYPES = frozenset({
    "text/xml",
    "application/xml",
    "application/x-xml",
})


@dataclass
class Attachment:
    filename: str
    content: bytes
    content_type: str
    size_bytes: int


# Backward-compat alias used by s3_uploader and any external callers.
PDFAttachment = Attachment


def extract_attachments(msg: Message, file_type: str = "pdf") -> List[Attachment]:
    """
    Walk all MIME parts of an email and return attachments of the requested type.

    file_type:
      "pdf"  – return only PDF attachments (validated by %PDF magic bytes)
      "xml"  – return only XML attachments (detected by content-type or .xml extension)
      "both" – return both PDF and XML attachments

    Handles:
    - Direct attachments (any Content-Type; PDFs validated by magic bytes).
    - ZIP attachments: opened in memory; relevant members extracted.
    - Nested ZIPs inside ZIPs (one level deep).
    """
    want_pdf = file_type in ("pdf", "both")
    want_xml = file_type in ("xml", "both")

    attachments: List[Attachment] = []
    part_summaries: List[str] = []

    for part in msg.walk():
        maintype = part.get_content_maintype()
        ct = part.get_content_type().lower()
        filename = _decode_filename(part)

        if maintype in _SKIP_MAINTYPE:
            continue
        # Skip text/* unless it's an XML type we actually want.
        if maintype == "text":
            if not (want_xml and _is_xml_type(ct, filename)):
                continue

        payload = part.get_payload(decode=True)
        if not payload:
            part_summaries.append(f"{ct}[fn={filename or '-'},EMPTY]")
            continue

        if _is_zip(ct, filename):
            extracted = _extract_from_zip(payload, filename or "attachment.zip", want_pdf, want_xml)
            attachments.extend(extracted)
            part_summaries.append(
                f"{ct}[fn={filename or '-'},sz={len(payload)}"
                f",zip→{len(extracted)} file(s)]"
            )
        else:
            is_xml = _is_xml_type(ct, filename)

            if want_pdf and not is_xml:
                att = _try_pdf_by_magic(payload, ct, filename, part_summaries)
                if att:
                    attachments.append(att)

            if want_xml and is_xml:
                att = _make_xml_attachment(payload, ct, filename)
                attachments.append(att)
                part_summaries.append(f"{ct}[fn={filename or '-'},sz={len(payload)},XML]")

    if not attachments and logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "No %s found — MIME parts: %s",
            file_type.upper() + "s",
            " | ".join(part_summaries) or "(none)",
        )

    return attachments


def extract_pdf_attachments(msg: Message) -> List[Attachment]:
    """Backward-compat wrapper — extracts PDF attachments only."""
    return extract_attachments(msg, file_type="pdf")


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _is_zip(content_type: str, filename: str) -> bool:
    if content_type in _ZIP_TYPES:
        return True
    if filename and filename.lower().endswith(".zip"):
        return True
    return False


def _is_xml_type(content_type: str, filename: str) -> bool:
    if content_type in _XML_CONTENT_TYPES:
        return True
    if filename and filename.lower().endswith(".xml"):
        return True
    return False


def _extract_from_zip(
    zip_bytes: bytes, zip_filename: str, want_pdf: bool, want_xml: bool
) -> List[Attachment]:
    """Open a ZIP in memory and return requested file types found inside."""
    results: List[Attachment] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                name = member.filename
                name_lower = name.lower()
                is_pdf_member = name_lower.endswith(".pdf")
                is_xml_member = name_lower.endswith(".xml")

                if not ((want_pdf and is_pdf_member) or (want_xml and is_xml_member)):
                    continue

                try:
                    file_bytes = zf.read(member)
                except Exception as exc:
                    logger.warning("Could not read %s from ZIP %s: %s", name, zip_filename, exc)
                    continue

                base = name.replace("\\", "/").rsplit("/", 1)[-1] or "attachment"

                if is_pdf_member and want_pdf:
                    if _PDF_MAGIC not in file_bytes[:_PDF_MAGIC_WINDOW]:
                        logger.warning(
                            "Member %s in ZIP %s has .pdf extension but no %%PDF magic — skipping",
                            name, zip_filename,
                        )
                        continue
                    results.append(Attachment(
                        filename=base,
                        content=file_bytes,
                        content_type="application/pdf",
                        size_bytes=len(file_bytes),
                    ))
                    logger.debug("Extracted PDF %s (%d bytes) from ZIP %s", base, len(file_bytes), zip_filename)

                elif is_xml_member and want_xml:
                    results.append(Attachment(
                        filename=base,
                        content=file_bytes,
                        content_type="application/xml",
                        size_bytes=len(file_bytes),
                    ))
                    logger.debug("Extracted XML %s (%d bytes) from ZIP %s", base, len(file_bytes), zip_filename)

    except zipfile.BadZipFile:
        logger.warning("Part is not a valid ZIP file: %s", zip_filename)
    except Exception as exc:
        logger.warning("Error reading ZIP %s: %s", zip_filename, exc)
    return results


def _make_xml_attachment(payload: bytes, content_type: str, filename: str) -> Attachment:
    effective_filename = filename or "attachment.xml"
    logger.debug("Found XML: %s (%d bytes) [content-type=%s]", effective_filename, len(payload), content_type)
    return Attachment(
        filename=effective_filename,
        content=payload,
        content_type=content_type,
        size_bytes=len(payload),
    )


def _try_pdf_by_magic(
    payload: bytes,
    content_type: str,
    filename: str,
    summaries: List[str],
) -> "Attachment | None":
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
    return Attachment(
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
