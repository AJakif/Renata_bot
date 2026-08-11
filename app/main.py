"""FastAPI application — POST /ask and static UI."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import chromadb
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.generate import Generator, stub_generate
from app.parser import parse_pdf
from app.retrieve import build_collection
from app.retrieve import retrieve as _retrieve

DOCS_DIR = Path(__file__).parent.parent / "docs"
CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"
STATIC_DIR = Path(__file__).parent.parent / "static"
TOP_K: int = int(os.getenv("TOP_K", "5"))

# Initialized during lifespan startup; overridable in tests via
# app.dependency_overrides[get_collection].
_collection: chromadb.Collection | None = None


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


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
    """Parse all PDFs in DOCS_DIR, embed them, and initialise the collection."""
    global _collection
    chunks = []
    for pdf in sorted(DOCS_DIR.glob("*.pdf")):
        chunks.extend(parse_pdf(pdf))
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _collection = build_collection(chunks, client)
    yield
    _collection = None


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
    chunks = _retrieve(body.question, collection, top_k=TOP_K)

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
