"""Unit tests for the product-scope filter (app/scope.py).

Pure function tests — no FastAPI, no embeddings, no LLM.
"""

from app.parser import ProductInfo
from app.retrieve import ChunkResult
from app.scope import filter_by_product_scope

# -- Fixtures -----------------------------------------------------------------

_ROLAC_INFO = ProductInfo(
    source="rolac_10mg_ketorolac_leaflet.pdf",
    brand="Rolac 10 mg",
    active_ingredient="Ketorolac Tromethamine 10 mg",
)

_MAXPRO_INFO = ProductInfo(
    source="maxpro_20mg_esomeprazole_leaflet.pdf",
    brand="Maxpro 20 mg",
    active_ingredient="Esomeprazole 20 mg",
)

_FENADIN_INFO = ProductInfo(
    source="fenadin_120mg_fexofenadine_leaflet.pdf",
    brand="Fenadin 120 mg",
    active_ingredient="Fexofenadine Hydrochloride 120 mg",
)

_ROLIP_INFO = ProductInfo(
    source="rolip_10mg_rosuvastatin_leaflet.pdf",
    brand="Rolip 10 mg",
    active_ingredient="Rosuvastatin (as Rosuvastatin Calcium) 10 mg",
)

_ALL_INFOS = [_ROLAC_INFO, _MAXPRO_INFO, _FENADIN_INFO, _ROLIP_INFO]


def _chunk(source: str, section: str = "What it is used for") -> ChunkResult:
    return ChunkResult(source=source, section=section, score=0.7, body="body text")


_ROLAC_CHUNK = _chunk(_ROLAC_INFO.source)
_MAXPRO_CHUNK = _chunk(_MAXPRO_INFO.source)
_FENADIN_CHUNK = _chunk(_FENADIN_INFO.source)
_ROLIP_CHUNK = _chunk(_ROLIP_INFO.source)

_ALL_CHUNKS = [_ROLAC_CHUNK, _MAXPRO_CHUNK, _FENADIN_CHUNK, _ROLIP_CHUNK]

# -- Tests --------------------------------------------------------------------


def test_no_product_named_returns_all_chunks() -> None:
    """Generic question naming no product leaves the chunk list unmodified."""
    result = filter_by_product_scope(
        "What are common side effects of pain medication?", _ALL_CHUNKS, _ALL_INFOS
    )
    assert result == _ALL_CHUNKS


def test_filter_by_brand_name() -> None:
    """Brand name in question scopes to that product only."""
    result = filter_by_product_scope("Can I drive after taking Fenadin?", _ALL_CHUNKS, _ALL_INFOS)
    assert result == [_FENADIN_CHUNK]


def test_filter_by_active_ingredient_ketorolac() -> None:
    """Active ingredient 'Ketorolac' alone scopes to Rolac."""
    result = filter_by_product_scope(
        "Is Ketorolac safe for elderly patients?", _ALL_CHUNKS, _ALL_INFOS
    )
    assert result == [_ROLAC_CHUNK]


def test_filter_by_active_ingredient_esomeprazole() -> None:
    """Active ingredient 'Esomeprazole' alone scopes to Maxpro."""
    result = filter_by_product_scope("How does Esomeprazole work?", _ALL_CHUNKS, _ALL_INFOS)
    assert result == [_MAXPRO_CHUNK]


def test_filter_by_active_ingredient_rosuvastatin() -> None:
    """Active ingredient 'Rosuvastatin' scopes to Rolip (despite 'as' connector in string)."""
    result = filter_by_product_scope(
        "What are the side effects of Rosuvastatin?", _ALL_CHUNKS, _ALL_INFOS
    )
    assert result == [_ROLIP_CHUNK]


def test_unknown_drug_name_not_filtered() -> None:
    """A plausible but unknown product name matches nothing — all chunks pass through."""
    result = filter_by_product_scope("Can I take Aspirex with food?", _ALL_CHUNKS, _ALL_INFOS)
    assert result == _ALL_CHUNKS


def test_multiple_products_named_both_pass() -> None:
    """Question naming two products allows chunks from both."""
    result = filter_by_product_scope(
        "Can I take Rolac and Fenadin together?", _ALL_CHUNKS, _ALL_INFOS
    )
    assert set(r.source for r in result) == {_ROLAC_INFO.source, _FENADIN_INFO.source}


def test_empty_product_infos_returns_unfiltered() -> None:
    """Empty registry (no products known) returns chunks unmodified."""
    result = filter_by_product_scope("What is Fenadin used for?", _ALL_CHUNKS, [])
    assert result == _ALL_CHUNKS


def test_unit_words_not_matched() -> None:
    """Numeric tokens and unit words ('mg', '10') do not trigger product matching."""
    result = filter_by_product_scope("Give me 10 mg dosage information.", _ALL_CHUNKS, _ALL_INFOS)
    # "10" and "mg" appear in every product — must not match any product.
    assert result == _ALL_CHUNKS


def test_matching_is_case_insensitive() -> None:
    """Brand/ingredient matching is case-insensitive."""
    result = filter_by_product_scope("what is fenadin?", _ALL_CHUNKS, _ALL_INFOS)
    assert result == [_FENADIN_CHUNK]


def test_generic_salt_word_does_not_scope() -> None:
    """A shared salt/complexing-agent word ('Calcium') must not scope the question.

    Rolip's active ingredient string is "Rosuvastatin (as Rosuvastatin Calcium)":
    "Calcium" is a generic complexing agent, not a product-identifying term, and
    is common enough to appear in an unrelated question — it must not narrow
    the corpus to Rolip alone.
    """
    result = filter_by_product_scope(
        "Can I take a calcium supplement with my medication?", _ALL_CHUNKS, _ALL_INFOS
    )
    assert result == _ALL_CHUNKS


def test_shared_salt_suffix_does_not_scope() -> None:
    """A salt suffix shared by two unrelated products ('Hydrochloride') must not scope.

    Doxicap ("Doxycycline Hydrochloride") and Fenadin ("Fexofenadine
    Hydrochloride") both contain "Hydrochloride" — only the leading compound
    word is a match term, so a query naming just the salt must stay unfiltered
    rather than silently narrowing to those two products.
    """
    result = filter_by_product_scope(
        "Is hydrochloride safe for elderly patients?", _ALL_CHUNKS, _ALL_INFOS
    )
    assert result == _ALL_CHUNKS


def test_word_boundary_prevents_partial_match() -> None:
    """A term that is a substring of another word does not match (e.g. 'Rolac' in 'Rolactin')."""
    result = filter_by_product_scope(
        "Can I take Rolactin during pregnancy?", _ALL_CHUNKS, _ALL_INFOS
    )
    # "Rolactin" is not "Rolac" — \b boundary prevents the match.
    assert result == _ALL_CHUNKS
