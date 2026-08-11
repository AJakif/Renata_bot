"""Parse leaflet PDFs into one Chunk per numbered section."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Matches the trailing legal disclaimer common to all leaflets.
# ~20 words: "Source: product information from MedEx ..."
_DISCLAIMER_RE = re.compile(
    r"\bSource:\s+product information\b.*",
    re.IGNORECASE | re.DOTALL,
)

# Matches numbered section headings: "1. Section name"
_HEADING_RE = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    """A single retrievable unit of text from a leaflet."""

    source: str  # PDF filename including .pdf extension
    section: str  # Section heading (exact, as it appears in the leaflet)
    body: str  # Section text (without the heading line)


def _extract_text(pdf_path: Path) -> str:
    """Run pdftotext -layout and return the raw text.

    Captures bytes and decodes as UTF-8 first, falling back to cp1252.
    pdftotext 4.00 on Windows emits cp1252 for non-ASCII bytes in some PDFs
    even though its default is nominally UTF-8.
    """
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"pdftotext failed for {pdf_path.name}: {stderr}")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return result.stdout.decode("cp1252")


def parse_pdf(pdf_path: Path) -> list[Chunk]:
    """Parse a leaflet PDF into chunks — one per section, plus a §0 overview.

    Traps handled:
    - Form feeds (\\f) immediately before some headings are stripped first.
    - Trailing legal disclaimer is stripped.
    - Absence of numbered sections raises ValueError (fail loudly).
    """
    text = _extract_text(pdf_path)
    # Strip form feeds before heading detection — they appear immediately
    # before some headings and break naive `^\\d+\\.` matching.
    text = text.replace("\f", "")
    # Strip the trailing legal disclaimer (identical across all leaflets).
    text = _DISCLAIMER_RE.sub("", text).strip()

    filename = pdf_path.name
    headings = list(_HEADING_RE.finditer(text))

    if not headings:
        raise ValueError(
            f"No numbered sections found in {filename}. "
            "Check that pdftotext extracted meaningful text."
        )

    chunks: list[Chunk] = []

    # §0 Product overview: the header block above section 1 (brand, ingredient,
    # form, manufacturer, pack, price). Unretrievable without this chunk.
    overview_body = text[: headings[0].start()].strip()
    if overview_body:
        chunks.append(Chunk(source=filename, section="Product overview", body=overview_body))

    # §1–N numbered sections
    for idx, match in enumerate(headings):
        section_name = match.group(2).strip()
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        body = text[body_start:body_end].strip()
        chunks.append(Chunk(source=filename, section=section_name, body=body))

    return chunks
