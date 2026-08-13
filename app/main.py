"""FastAPI application — POST /ask and static UI."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import chromadb
import numpy as np
import numpy.typing as npt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from app.generate import Generator, ProviderError, make_generator, parse_generation_result
from app.parser import Chunk, ProductInfo, extract_product_info, parse_pdf
from app.retrieve import build_bm25_index, build_body_vectors, build_collection
from app.retrieve import retrieve as _retrieve
from app.scope import filter_by_product_scope

# Load .env after all imports but before any os.getenv() constant definitions.
# Local app imports don't call os.getenv at import time, so their position
# relative to load_dotenv() does not affect the values they see.
load_dotenv()

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).parent.parent / "docs"
CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"
STATIC_DIR = Path(__file__).parent.parent / "static"
TOP_K: int = int(os.getenv("TOP_K", "8"))
CONTEXTUAL_HEADERS: bool = os.getenv("CONTEXTUAL_HEADERS", "true").lower() not in (
    "0",
    "false",
    "no",
)
HYBRID_RETRIEVAL: bool = os.getenv("HYBRID_RETRIEVAL", "true").lower() not in (
    "0",
    "false",
    "no",
)
DUAL_EMBEDDING: bool = os.getenv("DUAL_EMBEDDING", "true").lower() not in (
    "0",
    "false",
    "no",
)
# Provisional similarity gate threshold (layer 1 of grounding).  Sits in the measured
# gap: on-topic body-only cosines 0.51–0.85, off-topic 0.01–0.18.  Formal calibration
# against the 8-row eval set (D10) is pending — adjust via SIMILARITY_THRESHOLD env var.
SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.35"))

REFUSAL_MESSAGE: str = "I don't have enough information in the leaflets to answer that question."

# Structural invariant checked at startup: every document must yield exactly
# one overview chunk plus six numbered sections. The total is derived from the
# number of PDFs actually found, not hardcoded, so dropping a new leaflet into
# docs/ and restarting requires no code change (adding a doc changes doc count,
# not this per-document invariant).
_CHUNKS_PER_DOC = 7  # 1 overview + 6 numbered sections
_EXPECTED_SECTIONS_PER_DOC = 6

# Initialized during lifespan startup; overridable in tests via
# app.dependency_overrides[get_collection].
_collection: chromadb.Collection | None = None

# Active LLM generator constructed from LLM_PROVIDER env var during lifespan.
# Overridden in tests via app.dependency_overrides[get_generator].
_generator: Generator | None = None

# BM25 index and parallel chunk-id list built at startup alongside the Chroma
# collection.  Both are None/empty when HYBRID_RETRIEVAL=false or in tests that
# bypass lifespan — retrieve() degrades to dense-only when bm25 is None.
_bm25_index: BM25Okapi | None = None
_bm25_chunk_ids: list[str] = []

# Body-only vector store: chunk_id → L2-normalised float32 embedding of body
# text alone (no contextual header).  Built at startup when DUAL_EMBEDDING=true;
# {} otherwise.  Override in tests via app.dependency_overrides[get_body_vectors].
_body_vectors: dict[str, npt.NDArray[np.float32]] = {}

# Per-product brand/ingredient registry built at startup alongside the chunks.
# Consumed by future issues (e.g. product-info endpoint); not served directly.
_product_infos: list[ProductInfo] = []


def get_body_vectors() -> dict[str, npt.NDArray[np.float32]]:
    """FastAPI dependency that returns the active body-only vector store."""
    return _body_vectors


def get_product_infos() -> list[ProductInfo]:
    """FastAPI dependency that returns the per-product brand/ingredient registry."""
    return _product_infos


def get_collection() -> chromadb.Collection:
    """FastAPI dependency that returns the active ChromaDB collection."""
    if _collection is None:
        raise RuntimeError("Collection not initialized — server startup failed")
    return _collection


def get_generator() -> Generator:
    """FastAPI dependency that returns the active LLM generator.

    The generator is constructed once during lifespan startup from the
    LLM_PROVIDER env var.  Override in tests via
    ``app.dependency_overrides[get_generator]``.
    """
    if _generator is None:
        raise RuntimeError("Generator not initialized — server startup failed")
    return _generator


def _assert_chunks_valid(chunks: list[Chunk], pdf_paths: list[Path]) -> None:
    """Raise RuntimeError if chunk count or per-document section coverage is wrong.

    Called once at startup so a bad ingest prevents the app from serving stale
    or incomplete data.  parse_pdf already validates each document individually;
    this aggregates across all documents.
    """
    if not pdf_paths:
        raise RuntimeError(
            f"Startup assertion failed: no PDFs found in {DOCS_DIR}.  "
            "Check that docs/ is mounted/populated."
        )
    expected_total = len(pdf_paths) * _CHUNKS_PER_DOC
    if len(chunks) != expected_total:
        raise RuntimeError(
            f"Startup assertion failed: expected {expected_total} total chunks "
            f"({len(pdf_paths)} docs x {_CHUNKS_PER_DOC}), got {len(chunks)}.  "
            "Check that all PDFs ingested correctly."
        )
    for pdf_path in pdf_paths:
        fname = pdf_path.name
        numbered = [c for c in chunks if c.source == fname and c.section != "Product overview"]
        if len(numbered) != _EXPECTED_SECTIONS_PER_DOC:
            raise RuntimeError(
                f"Startup assertion failed: {fname} has {len(numbered)} numbered sections, "
                f"expected {_EXPECTED_SECTIONS_PER_DOC}."
            )
    logger.info(
        "Startup assertion passed: %d chunks across %d documents.",
        len(chunks),
        len(pdf_paths),
    )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
    """Parse all PDFs in DOCS_DIR, embed them, and initialise the collection."""
    global _collection, _product_infos, _bm25_index, _bm25_chunk_ids, _body_vectors, _generator
    _generator = make_generator()
    chunks: list[Chunk] = []
    pdf_paths = sorted(DOCS_DIR.glob("*.pdf"))
    for pdf in pdf_paths:
        doc_chunks = parse_pdf(pdf)
        chunks.extend(doc_chunks)
        _product_infos.append(extract_product_info(doc_chunks))
    _assert_chunks_valid(chunks, pdf_paths)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    product_infos_by_source = {pi.source: pi for pi in _product_infos}
    _collection = build_collection(
        chunks,
        client,
        product_infos_by_source,
        use_contextual_headers=CONTEXTUAL_HEADERS,
    )
    if HYBRID_RETRIEVAL:
        _bm25_index, _bm25_chunk_ids = build_bm25_index(
            chunks,
            product_infos_by_source,
            use_contextual_headers=CONTEXTUAL_HEADERS,
        )
    if DUAL_EMBEDDING:
        _body_vectors = build_body_vectors(chunks)
    yield
    _collection = None
    _generator = None
    _bm25_index = None
    _bm25_chunk_ids = []
    _body_vectors = {}
    _product_infos.clear()


app = FastAPI(title="Leaflet Assistant", lifespan=lifespan)


# ── Request / Response schemas ────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str


class Citation(BaseModel):
    source: str  # PDF filename including .pdf extension
    section: str  # Exact section heading
    score: float  # Cosine similarity ∈ [0, 1]


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the single-page UI."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    collection: Annotated[chromadb.Collection, Depends(get_collection)],
    generator: Annotated[Generator, Depends(get_generator)],
    body_vectors: Annotated[dict[str, npt.NDArray[np.float32]], Depends(get_body_vectors)],
    product_infos: Annotated[list[ProductInfo], Depends(get_product_infos)],
) -> AskResponse:
    """Retrieve relevant chunks, apply the similarity gate, and generate a grounded answer.

    Pipeline:
    1. Retrieve top-k chunks (hybrid or dense); when DUAL_EMBEDDING is on, each
       ChunkResult.score is the body-only cosine (D6/D7).
    2. Gate: if the best score is below SIMILARITY_THRESHOLD, refuse without
       calling the LLM (layer 1 of grounding).
    3. Build a numbered context prompt.
    4. Call the injected generator.
    5. Return answer + citations sorted by body-only cosine descending.
    """
    chunks = _retrieve(
        body.question,
        collection,
        top_k=TOP_K,
        bm25=_bm25_index,
        bm25_chunk_ids=_bm25_chunk_ids,
        use_hybrid=HYBRID_RETRIEVAL,
        body_vectors=body_vectors if DUAL_EMBEDDING else None,
        use_dual_embedding=DUAL_EMBEDDING,
    )
    chunks = filter_by_product_scope(body.question, chunks, product_infos)

    best_score = max((r.score for r in chunks), default=0.0)
    if best_score < SIMILARITY_THRESHOLD:
        return AskResponse(answer=REFUSAL_MESSAGE, citations=[])

    source_to_brand: dict[str, str] = {pi.source: pi.brand for pi in product_infos}
    context_blocks = "\n\n".join(
        f"[{i + 1}] ({source_to_brand.get(r.source, r.source)} — {r.section})\n{r.body}"
        for i, r in enumerate(chunks)
    )
    unique_sources = {r.source for r in chunks}
    # D14: when chunks span more than one product, instruct the LLM to attribute
    # each answer by product name.  Single-product queries are unaffected.
    multi_product_rule = (
        "If the passages above come from more than one product, structure your answer by "
        "product: begin each product's section with its name followed by a colon "
        "(e.g. 'Doxicap: ...') and answer independently for that product. "
        "Do not compare, rank, or contrast products.\n\n"
        if len(unique_sources) > 1
        else ""
    )
    prompt = (
        "You are a medicine information assistant. Answer the question using ONLY the numbered "
        "context passages below. Use no outside knowledge.\n\n"
        f"{context_blocks}\n\n"
        f"Question: {body.question}\n\n"
        f"{multi_product_rule}"
        "Respond with a single JSON object (no other text) matching this schema:\n"
        '{"answered": bool, "answer": string, "chunk_ids": [int, ...]}\n'
        "where chunk_ids lists the 1-based numbers of the passages you used. "
        'If the context does not answer the question, set answered to false, answer to "", '
        "and chunk_ids to []."
    )

    try:
        # generator() is a synchronous httpx call (up to `timeout` seconds); run it off
        # the event loop so a slow/hung provider doesn't stall other in-flight requests.
        raw = await asyncio.to_thread(generator, prompt, temperature=0.0)
    except ProviderError as exc:
        logger.error("LLM provider error — returning refusal: %s", exc)
        return AskResponse(answer=REFUSAL_MESSAGE, citations=[])
    result = parse_generation_result(raw)
    if result is None or not result.answered:
        return AskResponse(answer=REFUSAL_MESSAGE, citations=[])

    valid_ids = {i + 1 for i in range(len(chunks))}
    filtered_ids = [cid for cid in dict.fromkeys(result.chunk_ids) if cid in valid_ids]

    if not filtered_ids:
        logger.warning("cite-or-refuse: answered=True but no valid chunk_ids survived validation")
        return AskResponse(answer=REFUSAL_MESSAGE, citations=[])

    citations = sorted(
        [
            Citation(
                source=chunks[cid - 1].source,
                section=chunks[cid - 1].section,
                score=chunks[cid - 1].score,
            )
            for cid in filtered_ids
        ],
        key=lambda c: c.score,
        reverse=True,
    )
    return AskResponse(answer=result.answer, citations=citations)
