"""Seam-2 parser tests — real PDFs, no LLM.

All assertions are on observable behaviour: the chunks returned by parse_pdf
and the ProductInfo returned by extract_product_info.  Internal implementation
details (regex internals, page-join logic) are not tested.

Load-bearing tests (4):
  1. All 5 PDFs → 35 total chunks, 7 per document.
  2. No chunk body contains the trailing legal disclaimer.
  3. Heading-scheme mismatch raises a clear ValueError naming the file.
  4. extract_product_info returns parseable brand + active_ingredient for every doc.

Bonus (1): Maxpro dose-table survival — each dose-condition line is intact.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.parser import Chunk, extract_product_info, parse_pdf

DOCS_DIR = Path(__file__).parent.parent / "docs"
ALL_PDFS = sorted(DOCS_DIR.glob("*.pdf"))

# The five expected PDF filenames — fail fast if the fixture files are missing.
assert len(ALL_PDFS) == 5, f"Expected 5 PDFs in docs/, found {[p.name for p in ALL_PDFS]}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_chunks() -> list[Chunk]:
    chunks: list[Chunk] = []
    for pdf in ALL_PDFS:
        chunks.extend(parse_pdf(pdf))
    return chunks


# ---------------------------------------------------------------------------
# Load-bearing test 1: chunk count and per-document structure
# ---------------------------------------------------------------------------


def test_all_five_pdfs_yield_35_chunks() -> None:
    """5 docs × 7 chunks (1 overview + 6 sections) = 35 total."""
    chunks = _all_chunks()
    assert len(chunks) == 35, (
        f"Expected 35 chunks total, got {len(chunks)}. "
        f"Per-doc: { {p.name: sum(1 for c in chunks if c.source == p.name) for p in ALL_PDFS} }"
    )
    for pdf in ALL_PDFS:
        doc_chunks = [c for c in chunks if c.source == pdf.name]
        assert len(doc_chunks) == 7, f"{pdf.name}: expected 7 chunks, got {len(doc_chunks)}"
        sections = {c.section for c in doc_chunks}
        assert "Product overview" in sections, f"{pdf.name}: missing Product overview chunk"
        numbered = [c for c in doc_chunks if c.section != "Product overview"]
        assert len(numbered) == 6, f"{pdf.name}: expected 6 numbered sections, got {len(numbered)}"


# ---------------------------------------------------------------------------
# Load-bearing test 2: disclaimer stripped from all documents
# ---------------------------------------------------------------------------


def test_no_chunk_contains_disclaimer() -> None:
    """The trailing legal disclaimer must not appear in any chunk body."""
    chunks = _all_chunks()
    for chunk in chunks:
        assert "Source: product information" not in chunk.body, (
            f"Disclaimer found in {chunk.source!r} section {chunk.section!r}"
        )


# ---------------------------------------------------------------------------
# Load-bearing test 3: heading-scheme mismatch raises ValueError
# ---------------------------------------------------------------------------


def test_heading_scheme_mismatch_raises_valueerror() -> None:
    """A document missing section 3 must raise ValueError naming the file."""
    # Sections 1, 2, 4, 5, 6 — section 3 absent.
    fake_text = (
        "Brand name   Fakecap 10 mg\n"
        "Active ingredient   Fakestatin 10 mg\n\n"
        "1. What Fakecap is and what it is used for\n\nIt does things.\n\n"
        "2. Before you take Fakecap\n\nCheck with your doctor.\n\n"
        "4. Possible side effects\n\nNausea.\n\n"
        "5. Use in pregnancy and breast-feeding\n\nAsk your doctor.\n\n"
        "6. How to store Fakecap\n\nStore below 25°C.\n"
    )
    pdf_path = DOCS_DIR / "doxicap_100mg_doxycycline_leaflet.pdf"  # real file, text patched
    with (
        patch("app.parser._extract_text", return_value=fake_text),
        pytest.raises(ValueError, match="doxicap_100mg_doxycycline_leaflet.pdf"),
    ):
        parse_pdf(pdf_path)


# ---------------------------------------------------------------------------
# Load-bearing test 4: extract_product_info parses brand + ingredient
# ---------------------------------------------------------------------------


def test_extract_product_info_all_pdfs() -> None:
    """Each PDF yields parseable ProductInfo with non-empty brand and ingredient."""
    for pdf in ALL_PDFS:
        chunks = parse_pdf(pdf)
        info = extract_product_info(chunks)
        assert info.source == pdf.name
        assert info.brand, f"{pdf.name}: empty brand"
        assert info.active_ingredient, f"{pdf.name}: empty active_ingredient"
        # Sanity: brand should start with the product name (first word of filename)
        product_stem = pdf.stem.split("_")[0].capitalize()  # e.g. "Doxicap"
        assert product_stem.lower() in info.brand.lower(), (
            f"{pdf.name}: brand {info.brand!r} does not contain {product_stem!r}"
        )
        # No overview field value should be truncated mid-parenthesis or
        # mid-word by a continuation line (regression: Rolip's two-line
        # "Rosuvastatin (as Rosuvastatin\n  Calcium) 10 mg" used to be cut at
        # "Calcium" because a capitalized continuation word was mistaken for
        # the start of the next field).
        assert info.active_ingredient.count("(") == info.active_ingredient.count(")"), (
            f"{pdf.name}: unbalanced parens in active_ingredient {info.active_ingredient!r}"
        )


def test_rolip_active_ingredient_not_truncated() -> None:
    """Rolip's ingredient wraps onto an indented continuation line in the PDF;
    it must be joined in full, not cut off at the wrap point."""
    pdf = DOCS_DIR / "rolip_10mg_rosuvastatin_leaflet.pdf"
    info = extract_product_info(parse_pdf(pdf))
    assert info.active_ingredient == "Rosuvastatin (as Rosuvastatin Calcium) 10 mg", (
        f"Rolip active_ingredient truncated or malformed: {info.active_ingredient!r}"
    )


# ---------------------------------------------------------------------------
# Bonus: Maxpro dose table integrity
# ---------------------------------------------------------------------------


def test_maxpro_dose_table_rows_intact() -> None:
    """Maxpro section 3 must contain each dose condition and dose on one line."""
    pdf_path = DOCS_DIR / "maxpro_20mg_esomeprazole_leaflet.pdf"
    chunks = parse_pdf(pdf_path)
    section3 = next(
        (c for c in chunks if "How to take" in c.section),
        None,
    )
    assert section3 is not None, "Maxpro 'How to take' section not found"
    body = section3.body
    # Each table row should have condition and dose on the same line.
    expected_rows = [
        ("Symptomatic GERD", "20 mg"),
        ("Healing of erosive", "20 or 40 mg"),
        ("Duodenal ulcer", "20 mg"),
        ("H. pylori eradication", "40 mg"),
        ("Zollinger-Ellison", "20"),
    ]
    lines = body.splitlines()
    for condition, dose in expected_rows:
        matching = [line for line in lines if condition in line and dose in line]
        assert matching, (
            f"No single line in Maxpro §3 contains both {condition!r} and {dose!r}. Body:\n{body}"
        )
