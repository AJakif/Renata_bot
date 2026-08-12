"""Dense and hybrid (BM25 + dense) retrieval over a ChromaDB collection."""

import functools
from dataclasses import dataclass

import chromadb
import chromadb.api
import numpy as np
import numpy.typing as npt
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from rank_bm25 import BM25Okapi

from app.parser import Chunk, ProductInfo

COLLECTION_NAME = "leaflets"

# Conventional RRF constant: score = 1/(k + rank). k=60 is the de-facto standard
# from Cormack et al. (2009); it dampens variance across ranks without heavily
# penalising items that only appear in one list.
_RRF_K = 60


@dataclass
class ChunkResult:
    """A retrieved chunk with its cosine similarity score."""

    source: str  # PDF filename including .pdf extension
    section: str  # Exact section heading
    # Cosine similarity ∈ [0, 1]. When dual embedding is enabled (D6, architecture
    # default), this is the body-only cosine — the value the similarity gate
    # thresholds on and the value reported as the citation score.  When dual
    # embedding is disabled (single-vector baseline mode, DUAL_EMBEDDING=false),
    # this is the cosine against the indexed vector — header+body when contextual
    # headers are on.  In both modes scores are clamped to [0, 1] and sorted
    # descending (D7).
    score: float
    body: str  # Section text


def _contextual_header(chunk: Chunk, product: ProductInfo) -> str:
    """Return the header prepended to a chunk's embedding input when headers are enabled."""
    return f"{product.brand} ({product.active_ingredient}) — {chunk.section}"


def _embed_text(
    chunk: Chunk,
    product_infos: dict[str, ProductInfo] | None,
    *,
    use_contextual_headers: bool,
) -> str:
    """Return the text to embed (and index for BM25) for a single chunk."""
    if use_contextual_headers and product_infos and chunk.source in product_infos:
        header = _contextual_header(chunk, product_infos[chunk.source])
        return f"{header}\n{chunk.body}"
    return chunk.body


@functools.lru_cache(maxsize=1)
def _get_embedding_function() -> DefaultEmbeddingFunction:
    """Return the shared MiniLM embedding function (ONNX model loaded once per process)."""
    return DefaultEmbeddingFunction()


def build_body_vectors(
    chunks: list[Chunk],
    embedding_function: DefaultEmbeddingFunction | None = None,
) -> dict[str, npt.NDArray[np.float32]]:
    """Embed body-only text for every chunk; return a dict keyed by chunk id.

    The chunk id format is ``"source::section"``, matching the Chroma collection ids.
    Body-only embeddings intentionally omit the contextual header, regardless of the
    CONTEXTUAL_HEADERS flag — these vectors are used for gating and citation scoring
    (D6/D7), not for retrieval ranking.

    MiniLM output is L2-normalised, so dot-product over these vectors equals cosine
    similarity without a separate normalisation step.
    """
    if not chunks:
        return {}
    ef = embedding_function if embedding_function is not None else _get_embedding_function()
    bodies = [c.body for c in chunks]
    raw = ef(bodies)
    return {
        f"{c.source}::{c.section}": np.array(vec, dtype=np.float32)
        for c, vec in zip(chunks, raw, strict=True)
    }


def build_collection(
    chunks: list[Chunk],
    client: chromadb.api.ClientAPI,
    product_infos: dict[str, ProductInfo] | None = None,
    *,
    use_contextual_headers: bool = True,
) -> chromadb.Collection:
    """Rebuild the Chroma collection from scratch on every startup.

    Always deletes any existing collection and recreates it from the chunks
    supplied by the caller (parsed from the current contents of docs/).  This
    ensures that dropping a new PDF into docs/ and restarting the app is
    sufficient to ingest it — no code change, no manual cache deletion.

    When ``use_contextual_headers`` is True and a ProductInfo entry exists for a
    chunk's source, the embedding input is ``"Brand (Ingredient) — Section\\nbody"``
    while the stored/returned document text remains the raw body.  This separates
    the two concerns: the index vector encodes product identity; the retrieved text
    stays verbatim for downstream body-only gating.

    When ``use_contextual_headers`` is False or no ProductInfo is available for a
    chunk, ``chunk.body`` is embedded directly (same behaviour as before this flag
    was introduced).

    IMPORTANT: metadata={"hnsw:space": "cosine"} is required.
    Without it, Chroma defaults to L2/Euclidean distance, and the returned
    distance values are NOT cosine similarities — the threshold logic breaks
    silently.
    """
    ef = _get_embedding_function()
    # Guarantee the collection exists first so delete_collection never raises,
    # then drop it and recreate clean.  The get_or_create call carries no data;
    # the real config (cosine space, embedding function) is set on create_collection.
    client.get_or_create_collection(name=COLLECTION_NAME)
    client.delete_collection(name=COLLECTION_NAME)
    collection: chromadb.Collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )
    if chunks:
        embed_texts = [
            _embed_text(c, product_infos, use_contextual_headers=use_contextual_headers)
            for c in chunks
        ]
        # Embeddings are computed manually so the stored document text (pure body)
        # can differ from what was embedded (header+body).  Chroma's auto-embed path
        # always stores and indexes the same string, which would expose the header in
        # retrieved results and break the body-only gating contract.
        embeddings = ef(embed_texts)
        collection.add(
            documents=[c.body for c in chunks],
            embeddings=embeddings,
            metadatas=[{"source": c.source, "section": c.section} for c in chunks],
            ids=[f"{c.source}::{c.section}" for c in chunks],
        )
    return collection


def build_bm25_index(
    chunks: list[Chunk],
    product_infos: dict[str, ProductInfo] | None = None,
    *,
    use_contextual_headers: bool = True,
) -> tuple[BM25Okapi, list[str]]:
    """Build a BM25 index over the same text as the dense embedding.

    The BM25 corpus mirrors the dense embedding input exactly: when contextual
    headers are enabled and product info is available, the indexed text is
    ``"Brand (Ingredient) — Section\\nbody"``.  This is critical because brand
    names only appear in the header block, not chunk bodies — BM25 resolves the
    product axis via those rare, high-IDF brand-name tokens.

    Returns ``(bm25_index, chunk_ids)`` where ``chunk_ids[i]`` is the Chroma id
    (``"source::section"``) of the i-th document in the BM25 corpus, preserving
    the mapping needed to look up cosine scores after fusion.
    """
    corpus: list[list[str]] = []
    chunk_ids: list[str] = []
    for c in chunks:
        text = _embed_text(c, product_infos, use_contextual_headers=use_contextual_headers)
        corpus.append(text.lower().split())
        chunk_ids.append(f"{c.source}::{c.section}")
    return BM25Okapi(corpus), chunk_ids


def _rrf_fuse(ranked_lists: list[list[str]], top_k: int) -> list[str]:
    """Reciprocal Rank Fusion over multiple ranked lists of chunk ids.

    Each list contributes ``1 / (_RRF_K + rank)`` to a chunk's aggregate score.
    Returns up to ``top_k`` chunk ids ordered by fused score descending.

    RRF scores are internal to this function and must never propagate to callers
    (D7): the reported ``score`` field in ChunkResult stays the cosine similarity.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
    fused = sorted(scores, key=lambda cid: -scores[cid])
    return fused[:top_k]


def _retrieve_dense(
    query_embedding: npt.NDArray[np.float32],
    collection: chromadb.Collection,
    n_total: int,
    top_k: int,
) -> list[ChunkResult]:
    """Dense-only retrieval using a pre-computed query embedding."""
    n = min(top_k, n_total)
    results = collection.query(
        query_embeddings=[query_embedding],  # type: ignore[arg-type]  # Chroma stubs too narrow
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )
    raw_docs = results.get("documents") or []
    raw_metas = results.get("metadatas") or []
    raw_dists = results.get("distances") or []
    if not raw_docs or not raw_metas or not raw_dists:
        return []
    docs = raw_docs[0]
    metas = raw_metas[0]
    dists = raw_dists[0]
    out: list[ChunkResult] = []
    for doc, meta, dist in zip(docs, metas, dists, strict=True):
        if meta is None:
            continue
        out.append(
            ChunkResult(
                source=str(meta["source"]),
                section=str(meta["section"]),
                score=round(1.0 - float(dist), 6),
                body=doc,
            )
        )
    out.sort(key=lambda r: (-r.score, r.section))
    return out


def _retrieve_hybrid(
    query: str,
    query_embedding: npt.NDArray[np.float32],
    collection: chromadb.Collection,
    bm25: BM25Okapi,
    bm25_chunk_ids: list[str],
    n_total: int,
    top_k: int,
) -> list[ChunkResult]:
    """Hybrid BM25+dense retrieval with RRF fusion.

    Queries the full corpus from Chroma (cheap at 35 chunks) to obtain cosine
    similarities for every candidate, then fuses with BM25 rankings via RRF.
    The final list is sorted by cosine descending (D7: ranking by RRF while
    reporting cosine would produce non-monotonic scores).
    """
    # Fetch all chunks — full corpus is small (35 items); this gives us cosine
    # scores for every candidate including those only BM25 surfaces.
    results = collection.query(
        query_embeddings=[query_embedding],  # type: ignore[arg-type]  # Chroma stubs too narrow
        n_results=n_total,
        include=["documents", "metadatas", "distances"],
    )
    raw_docs = results.get("documents") or []
    raw_metas = results.get("metadatas") or []
    raw_dists = results.get("distances") or []
    if not raw_docs or not raw_metas or not raw_dists:
        return []
    docs = raw_docs[0]
    metas = raw_metas[0]
    dists = raw_dists[0]

    # Build id → chunk data maps from Chroma results.
    cosine_by_id: dict[str, float] = {}
    doc_by_id: dict[str, str] = {}
    source_by_id: dict[str, str] = {}
    section_by_id: dict[str, str] = {}
    for doc, meta, dist in zip(docs, metas, dists, strict=True):
        if meta is None:
            continue
        chunk_id = f"{meta['source']}::{meta['section']}"
        cosine_by_id[chunk_id] = round(1.0 - float(dist), 6)
        doc_by_id[chunk_id] = doc
        source_by_id[chunk_id] = str(meta["source"])
        section_by_id[chunk_id] = str(meta["section"])

    # Dense ranking: all ids ordered by cosine descending.
    dense_ranked = sorted(cosine_by_id, key=lambda cid: -cosine_by_id[cid])

    # BM25 ranking: all ids ordered by BM25 score descending.
    bm25_scores = bm25.get_scores(query.lower().split())
    bm25_ranked = [
        bm25_chunk_ids[i]
        for i in sorted(range(len(bm25_chunk_ids)), key=lambda i: -float(bm25_scores[i]))
    ]

    # RRF fusion selects top_k ids. RRF scores stay internal (D7).
    selected_ids = _rrf_fuse([dense_ranked, bm25_ranked], top_k)

    out: list[ChunkResult] = []
    for chunk_id in selected_ids:
        if chunk_id not in cosine_by_id:
            # Defensive: BM25 index built from a different chunk set — skip.
            continue
        out.append(
            ChunkResult(
                source=source_by_id[chunk_id],
                section=section_by_id[chunk_id],
                score=cosine_by_id[chunk_id],
                body=doc_by_id[chunk_id],
            )
        )

    # Sort by cosine descending (D7): citation scores must be monotonically
    # decreasing, matching the ranking order the user sees.
    out.sort(key=lambda r: (-r.score, r.section))
    return out


def retrieve(
    query: str,
    collection: chromadb.Collection,
    *,
    top_k: int = 8,
    bm25: BM25Okapi | None = None,
    bm25_chunk_ids: list[str] | None = None,
    use_hybrid: bool = True,
    body_vectors: dict[str, npt.NDArray[np.float32]] | None = None,
    use_dual_embedding: bool = True,
) -> list[ChunkResult]:
    """Return up to top_k chunks sorted by cosine similarity descending.

    The query is encoded once per call via the shared MiniLM singleton and the
    resulting vector is reused for both Chroma retrieval and body-only similarity
    gating — no second encode pass.

    When ``use_hybrid`` is True and a BM25 index is supplied, fuses dense and
    keyword rankings with Reciprocal Rank Fusion before selecting the top_k
    results.  When ``use_hybrid`` is False or no BM25 index is supplied, falls
    back to pure dense retrieval.

    When ``use_dual_embedding`` is True and ``body_vectors`` is non-empty,
    rescores each result using body-only cosine similarity (D6/D7): the reported
    ``ChunkResult.score`` is the body-only cosine, clamped to [0, 1], and results
    are re-sorted by it descending.  This is the architecture default.

    When ``use_dual_embedding`` is False or ``body_vectors`` is empty/None, the
    reported score stays the indexed-vector cosine (single-vector baseline mode,
    for measuring whether dual embedding is worth keeping).
    """
    n_total = collection.count()
    if n_total == 0:
        return []

    # Encode the query once; reuse for Chroma retrieval and body-only dot products.
    ef = _get_embedding_function()
    query_embedding: npt.NDArray[np.float32] = np.array(ef([query])[0], dtype=np.float32)

    if use_hybrid and bm25 is not None and bm25_chunk_ids is not None:
        results = _retrieve_hybrid(
            query, query_embedding, collection, bm25, bm25_chunk_ids, n_total, top_k
        )
    else:
        results = _retrieve_dense(query_embedding, collection, n_total, top_k)

    # Dual-embedding rescoring: replace indexed-vector cosine with body-only cosine.
    if use_dual_embedding and body_vectors:
        rescored: list[ChunkResult] = []
        for r in results:
            chunk_id = f"{r.source}::{r.section}"
            bv = body_vectors.get(chunk_id)
            if bv is not None:
                # MiniLM output is L2-normalised → dot product == cosine similarity.
                # Clamp: cosine can be genuinely negative for dissimilar text (not
                # just FP rounding) — never report a negative citation score (D7).
                cosine = max(0.0, float(np.dot(query_embedding, bv)))
                cosine = round(cosine, 6)
            else:
                # Unreachable in practice: build_body_vectors and build_collection
                # key off the same chunks with the same "source::section" id, so
                # every retrieved id has a body vector. Fail loud rather than
                # silently reporting the inflated header+body cosine (D7).
                raise KeyError(
                    f"No body-only vector for retrieved chunk {chunk_id!r} — "
                    "body_vectors and the collection were built from different chunk sets."
                )
            rescored.append(
                ChunkResult(source=r.source, section=r.section, score=cosine, body=r.body)
            )
        rescored.sort(key=lambda x: (-x.score, x.section))
        return rescored

    return results
