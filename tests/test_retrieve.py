"""Seam 2 tests -- retrieve() with real MiniLM embeddings, no LLM.

Corpus deliberately mirrors the real collection's defining property: same-section
bodies that never name their own product.  Only the contextual header (injected
before embedding) carries product identity.

Tests assert which chunks a query returns (source/section), never the internal
form of the header string.
"""

import chromadb
import pytest
from rank_bm25 import BM25Okapi

from app.parser import Chunk, ProductInfo
from app.retrieve import build_bm25_index, build_collection, retrieve

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


@pytest.fixture(scope="module")
def hybrid_on_index() -> tuple[chromadb.Collection, BM25Okapi, list[str]]:
    """Collection + BM25 index with contextual headers for hybrid retrieval tests."""
    client = chromadb.EphemeralClient()
    collection = build_collection(_CHUNKS, client, _PRODUCT_INFOS, use_contextual_headers=True)
    bm25_index, chunk_ids = build_bm25_index(_CHUNKS, _PRODUCT_INFOS, use_contextual_headers=True)
    return collection, bm25_index, chunk_ids


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


# ── Hybrid retrieval tests ─────────────────────────────────────────────────────


def test_hybrid_product_named_query_ranks_product_first(
    hybrid_on_index: tuple[chromadb.Collection, BM25Okapi, list[str]],
) -> None:
    """Hybrid on: brand-name query ranks that product's chunk first.

    Both products share an identical "How to store" body.  Dense alone resolves
    this via the contextual header embedding; BM25 reinforces it through the
    rare, high-IDF brand-name token.  After RRF fusion, product_a must rank
    ahead of product_b for a query that names only Alphazol.
    """
    collection, bm25_index, chunk_ids = hybrid_on_index
    results = retrieve(
        "how should I store Alphazol",
        collection,
        bm25=bm25_index,
        bm25_chunk_ids=chunk_ids,
        use_hybrid=True,
    )
    assert results, "Expected at least one result"
    assert results[0].source == _PRODUCT_A, (
        f"Expected {_PRODUCT_A} ranked first with hybrid on; got {results[0].source!r}"
    )
    # Score must be a cosine value — no RRF-derived number (which would be ~1/61 ≈ 0.016).
    assert 0.0 <= results[0].score <= 1.0, f"Score {results[0].score!r} is not a cosine value"
    # Citations must be sorted cosine descending (D7 — non-monotonic scores read as a bug).
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True), f"Results not sorted by cosine: {scores}"


def test_hybrid_generic_query_spans_multiple_products(
    hybrid_on_index: tuple[chromadb.Collection, BM25Okapi, list[str]],
) -> None:
    """Hybrid on: query naming no product returns chunks from both products.

    A storage query without a brand name should retrieve the identical "How to
    store" section from both products — hybrid must not artificially collapse
    results to a single source.
    """
    collection, bm25_index, chunk_ids = hybrid_on_index
    results = retrieve(
        "storage instructions",
        collection,
        bm25=bm25_index,
        bm25_chunk_ids=chunk_ids,
        use_hybrid=True,
        top_k=2,  # Only the two "How to store" chunks should be top-2 for this query.
    )
    sources = {r.source for r in results}
    assert _PRODUCT_A in sources, f"Expected {_PRODUCT_A} in results; got sources: {sources}"
    assert _PRODUCT_B in sources, f"Expected {_PRODUCT_B} in results; got sources: {sources}"


def test_hybrid_off_ignores_bm25_index(
    hybrid_on_index: tuple[chromadb.Collection, BM25Okapi, list[str]],
) -> None:
    """use_hybrid=False degrades to dense-only even when BM25 index is supplied.

    Calling retrieve() with use_hybrid=False and a BM25 index must return the
    same results as calling it with no BM25 index at all — the flag disables
    the fusion path completely.
    """
    collection, bm25_index, chunk_ids = hybrid_on_index
    query = "storage"
    dense_results = retrieve(query, collection, top_k=4, use_hybrid=False)
    hybrid_off_results = retrieve(
        query,
        collection,
        bm25=bm25_index,
        bm25_chunk_ids=chunk_ids,
        top_k=4,
        use_hybrid=False,
    )
    assert [(r.source, r.section) for r in dense_results] == [
        (r.source, r.section) for r in hybrid_off_results
    ], "use_hybrid=False with BM25 supplied must match pure dense retrieval"
