"""Seam 2 tests -- retrieve() with real MiniLM embeddings, no LLM.

Corpus deliberately mirrors the real collection's defining property: same-section
bodies that never name their own product.  Only the contextual header (injected
before embedding) carries product identity.

Tests assert which chunks a query returns (source/section), never the internal
form of the header string.
"""

import chromadb
import pytest

from app.parser import Chunk, ProductInfo
from app.retrieve import build_collection, retrieve

_PRODUCT_A = "product_a.pdf"
_PRODUCT_B = "product_b.pdf"

# Bodies are intentionally identical and product-neutral — the collision case.
_STORE_BODY = "Keep in a cool, dry place away from sunlight."

_CHUNKS: list[Chunk] = [
    Chunk(source=_PRODUCT_A, section="How to store", body=_STORE_BODY),
    Chunk(source=_PRODUCT_B, section="How to store", body=_STORE_BODY),
    Chunk(
        source=_PRODUCT_A,
        section="What it is used for",
        body="Used to treat bacterial infections.",
    ),
    Chunk(
        source=_PRODUCT_B,
        section="What it is used for",
        body="Used to treat high blood pressure.",
    ),
]

_PRODUCT_INFOS: dict[str, ProductInfo] = {
    _PRODUCT_A: ProductInfo(
        source=_PRODUCT_A,
        brand="Alphazol 10 mg",
        active_ingredient="Alphamine",
    ),
    _PRODUCT_B: ProductInfo(
        source=_PRODUCT_B,
        brand="Betacor 20 mg",
        active_ingredient="Betamine",
    ),
}


@pytest.fixture(scope="module")
def headers_on_collection() -> chromadb.Collection:
    """In-memory collection built with contextual headers enabled."""
    client = chromadb.EphemeralClient()
    return build_collection(_CHUNKS, client, _PRODUCT_INFOS, use_contextual_headers=True)


@pytest.fixture(scope="module")
def headers_off_collection() -> chromadb.Collection:
    """In-memory collection built with contextual headers disabled."""
    client = chromadb.EphemeralClient()
    return build_collection(_CHUNKS, client, use_contextual_headers=False)


def test_headers_on_resolves_product_collision(headers_on_collection: chromadb.Collection) -> None:
    """Headers on: brand-name query retrieves that product's chunk ranked first.

    Both products have the same storage body, so without the header the retriever
    has no signal to prefer one over the other.  With the header the embedding
    encodes "Alphazol (Alphamine) — How to store" and the brand-name query pulls
    product_a ahead.
    """
    results = retrieve("how should I store Alphazol", headers_on_collection)
    assert results, "Expected at least one result"
    assert results[0].source == _PRODUCT_A, (
        f"Expected {_PRODUCT_A} ranked first; got {results[0].source!r}"
    )
    # The header must only influence the embedding, never the returned text —
    # a regression that stores header+body would still pass every ranking
    # assertion above, so the body must be checked directly.
    assert results[0].body == _STORE_BODY, (
        f"Expected raw body with no header leakage; got {results[0].body!r}"
    )


def test_headers_off_still_returns_results(headers_off_collection: chromadb.Collection) -> None:
    """Headers off: retrieve() is callable and returns results — flag does not break retrieval."""
    results = retrieve("how should I store", headers_off_collection)
    assert results, "Expected at least one result with use_contextual_headers=False"
    # All returned chunks must have a recognised source (basic sanity).
    sources = {r.source for r in results}
    assert sources <= {_PRODUCT_A, _PRODUCT_B}
