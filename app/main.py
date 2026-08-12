"""FastAPI application — POST /ask and static UI."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import chromadb
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from app.generate import Generator, stub_generate
from app.parser import Chunk, ProductInfo, extract_product_info, parse_pdf
from app.retrieve import build_bm25_index, build_collection
from app.retrieve import retrieve as _retrieve

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

# BM25 index and parallel chunk-id list built at startup alongside the Chroma
# collection.  Both are None/empty when HYBRID_RETRIEVAL=false or in tests that
# bypass lifespan — retrieve() degrades to dense-only when bm25 is None.
_bm25_index: BM25Okapi | None = None
_bm25_chunk_ids: list[str] = []

# Per-product brand/ingredient registry built at startup alongside the chunks.
# Consumed by future issues (e.g. product-info endpoint); not served directly.
_product_infos: list[ProductInfo] = []


def get_collection() -> chromadb.Collection:
    """FastAPI dependency that returns the active ChromaDB collection."""
    if _collection is None:
        raise RuntimeError("Collection not initialized — server startup failed")
    return _collection


def get_generator() -> Generator:
    """FastAPI dependency that returns the active LLM generator.

    Override this in tests via ``app.dependency_overrides[get_generator]``.
    In production, read ``LLM_PROVIDER`` here and return the real adapter
    (added in a later slice).
    """
    return stub_generate


def _assert_chunks_valid(chunks: list[Chunk], pdf_paths: list[Path]) -> None:
    """Raise RuntimeError if chunk count or per-document section coverage is wrong.

    Called once at startup so a bad ingest prevents the app from serving stale
    or incomplete data.  parse_pdf already validates each document individually;
    this aggregates across all documents.
    """
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
    global _collection, _product_infos, _bm25_index, _bm25_chunk_ids
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
    yield
    _collection = None
    _bm25_index = None
    _bm25_chunk_ids = []
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
) -> AskResponse:
    """Retrieve relevant chunks and generate a grounded answer.

    Pipeline (this slice — dense only, no gating):
    1. Retrieve top-k chunks by cosine similarity.
    2. Build a numbered context prompt.
    3. Call the injected generator.
    4. Return answer + citations sorted by score descending.
    """
    chunks = _retrieve(
        body.question,
        collection,
        top_k=TOP_K,
        bm25=_bm25_index,
        bm25_chunk_ids=_bm25_chunk_ids,
        use_hybrid=HYBRID_RETRIEVAL,
    )

    context_blocks = "\n\n".join(
        f"[{i + 1}] ({r.source} — {r.section})\n{r.body}" for i, r in enumerate(chunks)
    )
    prompt = (
        "Answer the question using ONLY the numbered context passages below. "
        "Be concise and accurate. Do not add information not present in the context.\n\n"
        f"{context_blocks}\n\nQuestion: {body.question}\nAnswer:"
    )

    answer = generator(prompt)
    citations = [Citation(source=r.source, section=r.section, score=r.score) for r in chunks]
    return AskResponse(answer=answer, citations=citations)
