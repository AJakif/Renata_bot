"""Dense retrieval over a ChromaDB collection using cosine similarity."""

from dataclasses import dataclass

import chromadb
import chromadb.api
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from app.parser import Chunk, ProductInfo

COLLECTION_NAME = "leaflets"


@dataclass
class ChunkResult:
    """A retrieved chunk with its cosine similarity score."""

    source: str  # PDF filename including .pdf extension
    section: str  # Exact section heading
    # Cosine similarity ∈ [0, 1] against the *indexed* vector — header+body when
    # contextual headers are enabled, body-only otherwise. NOT yet the body-only
    # gating score the architecture calls for; that split (dual embedding, D6 in
    # PLAN_AND_DECISIONS.md) is a separate, not-yet-implemented slice.
    score: float
    body: str  # Section text


def _contextual_header(chunk: Chunk, product: ProductInfo) -> str:
    """Return the header prepended to a chunk's embedding input when headers are enabled."""
    return f"{product.brand} ({product.active_ingredient}) — {chunk.section}"


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
    ef = DefaultEmbeddingFunction()
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
        embed_texts: list[str] = []
        for c in chunks:
            if use_contextual_headers and product_infos and c.source in product_infos:
                header = _contextual_header(c, product_infos[c.source])
                embed_texts.append(f"{header}\n{c.body}")
            else:
                embed_texts.append(c.body)
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


def retrieve(
    query: str,
    collection: chromadb.Collection,
    *,
    top_k: int = 5,
) -> list[ChunkResult]:
    """Return up to top_k chunks sorted by cosine similarity descending.

    Cosine space: Chroma returns distance = 1 - cosine_similarity, so
    score = 1 - distance.  Tie-breaking by section name keeps ordering
    deterministic across identical calls.
    """
    n = min(top_k, collection.count())
    if n == 0:
        return []
    results = collection.query(
        query_texts=[query],
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
    # Sort by score descending; stable tie-break on section name.
    out.sort(key=lambda r: (-r.score, r.section))
    return out
