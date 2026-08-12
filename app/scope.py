"""Product-scope filter: limits retrieved chunks to the named product(s).

Applied after retrieve() and before the similarity gate in app/main.py so
retrieval remains independently measurable without this filter (D13).
"""

import re

from app.parser import ProductInfo
from app.retrieve import ChunkResult

# Numeric tokens and SI unit abbreviations appear across all products and carry
# no product-identifying signal — "100 mg" would match every leaflet.
_UNIT_WORDS: frozenset[str] = frozenset({"mg", "ml", "mcg", "g", "kg", "iu"})


def _significant_words(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text) if not re.fullmatch(r"\d+", t)]


def _build_match_terms(info: ProductInfo) -> list[str]:
    """Extract word tokens from brand + active_ingredient that identify this product.

    Only the active ingredient's leading word (the base compound name, e.g.
    "Doxycycline" out of "Doxycycline Hydrochloride") is used — salts and
    complexing agents ("Hydrochloride", "Tromethamine", "Calcium") are shared
    across unrelated drugs and are generic enough to appear in an ordinary
    question, which would wrongly scope it to one product.
    """
    brand_words = [t for t in _significant_words(info.brand) if t.lower() not in _UNIT_WORDS]
    ingredient_words = _significant_words(info.active_ingredient)
    ingredient_term = ingredient_words[0] if ingredient_words else None
    return brand_words + ([ingredient_term] if ingredient_term else [])


def filter_by_product_scope(
    question: str,
    chunks: list[ChunkResult],
    product_infos: list[ProductInfo],
) -> list[ChunkResult]:
    """Remove chunks belonging to products not mentioned in *question*.

    If no product term is found in *question*, all chunks are returned
    unmodified so generic topic queries still retrieve across all leaflets.
    Matching uses word-boundary regex (case-insensitive); no fuzzy or NLP.
    """
    matched_sources: set[str] = set()
    for info in product_infos:
        terms = _build_match_terms(info)
        if any(re.search(rf"\b{re.escape(t)}\b", question, re.IGNORECASE) for t in terms):
            matched_sources.add(info.source)

    if not matched_sources:
        return chunks

    return [c for c in chunks if c.source in matched_sources]
