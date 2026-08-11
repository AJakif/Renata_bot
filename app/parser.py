"""Parse leaflet PDFs into one Chunk per numbered section."""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# Narrow, explicit translation table for typographic characters present in
# the corpus.  Not a general NFKD normalise — only the observed set is mapped
# so we don't mangle currency signs (৳), degree signs (°), or bullet (•).
_UNICODE_NORM_TABLE: dict[int, str] = {
    ord("–"): "-",  # en dash
    ord("—"): "-",  # em dash
    ord("‘"): "'",  # left single quotation mark
    ord("’"): "'",  # right single quotation mark
    ord("“"): '"',  # left double quotation mark
    ord("”"): '"',  # right double quotation mark
}

# Matches the trailing legal disclaimer common to all leaflets.
_DISCLAIMER_RE = re.compile(
    r"\bSource:\s+product information\b.*",
    re.IGNORECASE | re.DOTALL,
)

# Matches numbered section headings: "1. Section name"
_HEADING_RE = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)

# pypdf layout extraction sometimes runs a heading directly onto the preceding
# text without a newline (observed in doxicap §4, rolac §5).  Insert \n before
# any digit+dot+space that is not already at the start of a line.
_HEADING_NEWLINE_RE = re.compile(r"(?<!\n)(?=\d+\. )")


@dataclass
class Chunk:
    """A single retrievable unit of text from a leaflet."""

    source: str  # PDF filename including .pdf extension
    section: str  # Section heading (exact, as it appears in the leaflet)
    body: str  # Section text (without the heading line)


@dataclass
class ProductInfo:
    """Brand name and active ingredient extracted from the §0 overview chunk."""

    source: str  # PDF filename including .pdf extension
    brand: str  # e.g. "Doxicap 100 mg"
    active_ingredient: str  # e.g. "Doxycycline Hydrochloride 100 mg"


def _extract_text(pdf_path: Path) -> str:
    """Extract text from a PDF using pypdf with layout-preserving mode.

    ``extraction_mode="layout"`` preserves columnar content (e.g. the Maxpro
    dose table) row-for-row, keeping each condition and its dose on one line.
    pypdf is pure Python — no system binary (pdftotext/poppler) required.
    """
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout") or ""
        pages.append(text)
    # Join pages with a newline so page boundaries don't merge lines.
    return "\n".join(pages)


def _normalize_unicode(text: str) -> str:
    """Replace typographic characters with their ASCII equivalents."""
    return text.translate(_UNICODE_NORM_TABLE)


def _field_from_overview(body: str, label: str) -> str | None:
    """Extract a named field value from the §0 overview block.

    Handles values that continue onto the next indented line (e.g. Rolip's
    active ingredient spans two lines in the PDF).
    """
    pattern = rf"{re.escape(label)}\s+(.+?)(?=\n\s*[A-Z][a-z]|\Z)"
    m = re.search(pattern, body, re.DOTALL)
    if m is None:
        return None
    # Collapse internal whitespace from continuation lines.
    return re.sub(r"\s+", " ", m.group(1)).strip()


def extract_product_info(chunks: list[Chunk]) -> ProductInfo:
    """Derive brand and active ingredient from the §0 'Product overview' chunk.

    Raises ``ValueError`` if no overview chunk is present or the fields cannot
    be parsed.  This function is intentionally minimal — it is consumed by a
    future issue to build a per-product registry; no HTTP endpoint is exposed.
    """
    overview = next((c for c in chunks if c.section == "Product overview"), None)
    if overview is None:
        source = chunks[0].source if chunks else "unknown"
        raise ValueError(f"No 'Product overview' chunk found in {source}")
    body = overview.body
    brand = _field_from_overview(body, "Brand name")
    ingredient = _field_from_overview(body, "Active ingredient")
    if not brand or not ingredient:
        raise ValueError(
            f"Could not parse brand/ingredient from overview of {overview.source}. "
            f"Body (first 200 chars): {body[:200]!r}"
        )
    return ProductInfo(
        source=overview.source,
        brand=brand,
        active_ingredient=ingredient,
    )


def parse_pdf(pdf_path: Path) -> list[Chunk]:
    """Parse a leaflet PDF into chunks — one per section, plus a §0 overview.

    Traps handled:
    - Form feeds (\\f) immediately before some headings are stripped first.
    - pypdf may run heading text onto a preceding line without a newline;
      ``_HEADING_NEWLINE_RE`` inserts the missing newline before heading detection.
    - Typographic unicode characters are normalised to ASCII equivalents.
    - Trailing legal disclaimer is stripped.
    - A document without exactly sections 1-6 raises ``ValueError`` loudly
      rather than silently indexing partial content.
    """
    text = _extract_text(pdf_path)
    # Strip form feeds — pypdf may emit \f at page boundaries.
    text = text.replace("\f", "")
    # Ensure headings start on their own line (pypdf layout artefact).
    text = _HEADING_NEWLINE_RE.sub("\n", text)
    # Replace typographic unicode characters with ASCII equivalents.
    text = _normalize_unicode(text)
    # Strip trailing legal disclaimer (identical across all leaflets).
    text = _DISCLAIMER_RE.sub("", text).strip()

    filename = pdf_path.name
    headings = list(_HEADING_RE.finditer(text))

    if not headings:
        raise ValueError(
            f"No numbered sections found in {filename}. Check that pypdf extracted meaningful text."
        )

    # Enforce exactly sections 1 through 6 — fail loudly on any mismatch.
    section_numbers = [int(m.group(1)) for m in headings]
    expected = list(range(1, 7))
    if section_numbers != expected:
        missing = sorted(set(expected) - set(section_numbers))
        unexpected = sorted(set(section_numbers) - set(expected))
        raise ValueError(
            f"Heading-scheme mismatch in {filename}: expected sections 1-6, "
            f"got {section_numbers}."
            + (f" Missing: {missing}." if missing else "")
            + (f" Unexpected/out-of-range: {unexpected}." if unexpected else "")
        )

    chunks: list[Chunk] = []

    # §0 Product overview: header block above section 1 (brand, ingredient,
    # form, manufacturer, pack, price) — otherwise unretrievable.
    overview_body = text[: headings[0].start()].strip()
    if overview_body:
        chunks.append(Chunk(source=filename, section="Product overview", body=overview_body))

    # §1–6 numbered sections — one chunk per section.
    for idx, match in enumerate(headings):
        section_name = match.group(2).strip()
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        body = text[body_start:body_end].strip()
        chunks.append(Chunk(source=filename, section=section_name, body=body))

    return chunks
