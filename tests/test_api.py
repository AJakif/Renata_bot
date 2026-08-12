"""Seam 1 tests -- POST /ask with injectable stub generate.

No LLM/generation network calls are made: the stub generate() function avoids
that entirely.  The ChromaDB DefaultEmbeddingFunction (ONNX MiniLM) may
require a one-time model download on a completely fresh machine; once cached
locally all subsequent runs are fully offline.

These tests cover:
  AC1  response shape: {answer, citations[{source, section, score}]}
  AC1  source includes .pdf extension
  AC2  non-empty answer and at least one citation with correct section
  AC5  identical output for repeated identical questions (determinism)
  AC6  collection is created with cosine space explicitly configured
  Cite-or-refuse: unknown id discarded, all-invalid → refusal, mixed,
                  answered=False, answered=True with no valid ids (false refusal),
                  malformed output → refusal.
"""

import logging
from collections.abc import Generator as IterGenerator
from collections.abc import Iterator
from contextlib import contextmanager

import chromadb
import numpy as np
import numpy.typing as npt
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.generate import GenerationResult, Generator, stub_generate
from app.main import REFUSAL_MESSAGE, app, get_body_vectors, get_collection, get_generator
from app.parser import Chunk
from app.retrieve import build_body_vectors, build_collection

# -- Fixtures -----------------------------------------------------------------

_SAMPLE_CHUNKS: list[Chunk] = [
    Chunk(
        source="doxicap_100mg_doxycycline_leaflet.pdf",
        section="Product overview",
        body=(
            "Doxicap 100 mg. Active ingredient: Doxycycline Hydrochloride 100 mg. "
            "Form: Capsule. Manufacturer: Renata PLC, Bangladesh."
        ),
    ),
    Chunk(
        source="doxicap_100mg_doxycycline_leaflet.pdf",
        section="What Doxicap is and what it is used for",
        body=(
            "Doxicap contains doxycycline, a tetracycline antibiotic. "
            "It treats infections caused by susceptible microorganisms, including "
            "respiratory tract infections, gastrointestinal infections, chlamydial "
            "infections and sexually transmitted diseases, acne, brucellosis, and cellulitis."
        ),
    ),
    Chunk(
        source="doxicap_100mg_doxycycline_leaflet.pdf",
        section="Before you take Doxicap",
        body=(
            "Do not take if you: are hypersensitive to any tetracycline; "
            "are a child under 8 years of age; are pregnant; or are breast-feeding."
        ),
    ),
    Chunk(
        source="doxicap_100mg_doxycycline_leaflet.pdf",
        section="How to take Doxicap",
        body=(
            "Swallow the capsules whole with plenty of fluid, during meals, "
            "while sitting or standing upright. Usual dose: 200 mg on the first day, "
            "then 100 mg daily for 7-10 days."
        ),
    ),
    Chunk(
        source="doxicap_100mg_doxycycline_leaflet.pdf",
        section="How to store Doxicap",
        body="Store in a cool, dry place, protected from light. Keep out of the reach of children.",
    ),
]


@pytest.fixture(scope="session")
def ephemeral_collection() -> chromadb.Collection:
    """Build an in-memory collection from sample chunks (real embeddings, no LLM)."""
    client = chromadb.EphemeralClient()
    return build_collection(_SAMPLE_CHUNKS, client)


@pytest.fixture(scope="session")
def sample_body_vectors() -> dict[str, npt.NDArray[np.float32]]:
    """Body-only vectors for the sample chunks (real MiniLM embeddings)."""
    return build_body_vectors(_SAMPLE_CHUNKS)


@pytest.fixture()
def client(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
) -> IterGenerator[TestClient]:
    """TestClient with stubbed collection, generator, and body vectors (no LLM network calls).

    Intentionally NOT used as a context manager: the production lifespan
    (which parses PDFs and builds ChromaDB) must not run during tests.
    The collection, generator, and body_vectors are injected via dependency_overrides.
    """

    def _get_collection() -> chromadb.Collection:
        return ephemeral_collection

    def _get_generator() -> Generator:
        return stub_generate

    def _get_body_vectors() -> dict[str, npt.NDArray[np.float32]]:
        return sample_body_vectors

    app.dependency_overrides[get_collection] = _get_collection
    app.dependency_overrides[get_generator] = _get_generator
    app.dependency_overrides[get_body_vectors] = _get_body_vectors
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# -- Tests --------------------------------------------------------------------


def test_response_shape(client: TestClient) -> None:
    """AC1 -- Response has answer string and citations list with required fields."""
    resp = client.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["answer"], str)
    assert isinstance(data["citations"], list)
    assert len(data["citations"]) > 0
    for citation in data["citations"]:
        assert "source" in citation
        assert "section" in citation
        assert "score" in citation


def test_source_has_pdf_extension(client: TestClient) -> None:
    """AC1 -- Every citation source ends with .pdf."""
    resp = client.post("/ask", json={"question": "What is Doxicap?"})
    data = resp.json()
    for citation in data["citations"]:
        assert citation["source"].endswith(".pdf"), (
            f"source {citation['source']!r} does not end with .pdf"
        )


def test_relevant_section_retrieved(client: TestClient) -> None:
    """AC2 -- Storage question returns a citation naming the storage section."""
    resp = client.post("/ask", json={"question": "How should I store Doxicap?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"]  # non-empty answer
    sections = [c["section"] for c in data["citations"]]
    assert any("store" in s.lower() for s in sections), (
        f"Expected a storage section in citations; got: {sections}"
    )


def test_determinism(client: TestClient) -> None:
    """AC5 -- Identical question asked twice returns identical output."""
    payload = {"question": "What infections does Doxicap treat?"}
    r1 = client.post("/ask", json=payload).json()
    r2 = client.post("/ask", json=payload).json()
    assert r1 == r2, "Repeated identical question returned different results"


def test_cosine_space_configured(
    ephemeral_collection: chromadb.Collection,
) -> None:
    """AC6 -- Collection metadata confirms cosine distance space."""
    meta = ephemeral_collection.metadata
    assert meta is not None
    assert meta.get("hnsw:space") == "cosine", f"Expected hnsw:space=cosine; got metadata: {meta}"


def test_citations_sorted_score_descending(client: TestClient) -> None:
    """Citations are ordered from highest to lowest score (no non-monotonic values)."""
    resp = client.post("/ask", json={"question": "What is doxycycline used for?"})
    data = resp.json()
    scores = [c["score"] for c in data["citations"]]
    assert scores == sorted(scores, reverse=True), f"Citations not sorted descending: {scores}"


def test_citation_score_is_cosine_not_rrf(client: TestClient) -> None:
    """Citation.score is a cosine similarity ∈ [0, 1], never an RRF-derived value.

    RRF scores are ~1/(60+rank) ≈ 0.016 at best — if any citation score is that
    small for a relevant query it almost certainly leaked from the fusion layer (D7).
    All scores must be in [0, 1] and at least one must exceed 0.5 for an
    on-topic question against this corpus.
    """
    resp = client.post("/ask", json={"question": "What is Doxicap used for?"})
    data = resp.json()
    scores = [c["score"] for c in data["citations"]]
    assert all(0.0 <= s <= 1.0 for s in scores), f"Score(s) outside cosine range: {scores}"
    assert any(s > 0.5 for s in scores), (
        f"No score > 0.5 — scores look like RRF values, not cosine similarities: {scores}"
    )


def test_refusal_response_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate fires → response has the same shape as a normal answer but empty citations.

    SIMILARITY_THRESHOLD patched to an impossibly high value so every query is refused,
    letting us verify the refusal contract without needing a genuinely out-of-scope query.
    """
    monkeypatch.setattr(main_module, "SIMILARITY_THRESHOLD", 999.0)
    resp = client.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["answer"], str)
    assert isinstance(data["citations"], list)
    assert data["citations"] == [], f"Expected empty citations on refusal; got {data['citations']}"
    assert data["answer"] == REFUSAL_MESSAGE


def test_refusal_skips_generator(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the gate fires, the LLM generator is not called.

    stub_generate returns a fixed non-refusal string.  If the gate is bypassed the
    answer would equal that string; REFUSAL_MESSAGE proves the gate short-circuited.
    """
    monkeypatch.setattr(main_module, "SIMILARITY_THRESHOLD", 999.0)
    resp = client.post("/ask", json={"question": "What is Doxicap used for?"})
    data = resp.json()
    assert data["answer"] == REFUSAL_MESSAGE, (
        "Expected refusal message — generator appears to have been called despite gate"
    )
    assert data["answer"] != stub_generate("any prompt"), (
        "Refusal answer must differ from stub_generate output"
    )


def test_scores_never_negative(client: TestClient) -> None:
    """All citation scores are ≥ 0 (body-only cosines clamped, never negative)."""
    resp = client.post("/ask", json={"question": "What infections does Doxicap treat?"})
    data = resp.json()
    scores = [c["score"] for c in data["citations"]]
    assert all(s >= 0.0 for s in scores), f"Negative score(s) in citations: {scores}"


# -- Cite-or-refuse tests (layer 3 grounding) ---------------------------------


@contextmanager
def _client_with_generator(
    collection: chromadb.Collection,
    body_vectors: dict[str, npt.NDArray[np.float32]],
    gen: Generator,
) -> Iterator[TestClient]:
    """Context manager: TestClient with custom collection, body vectors, and generator."""

    app.dependency_overrides[get_collection] = lambda: collection
    app.dependency_overrides[get_generator] = lambda: gen
    app.dependency_overrides[get_body_vectors] = lambda: body_vectors
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.clear()


def test_unknown_id_discarded(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
) -> None:
    """Stub cites a valid id alongside an out-of-range id — only the valid id survives."""

    def _stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return GenerationResult(
            answered=True,
            answer="test answer",
            chunk_ids=[1, 999],
        ).model_dump_json()

    with _client_with_generator(ephemeral_collection, sample_body_vectors, _stub) as tc:
        resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "test answer"
    assert len(data["citations"]) == 1


def test_all_invalid_ids_causes_refusal(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
) -> None:
    """Stub cites only out-of-range ids → refusal; stub answer text absent from response."""
    stub_answer = "CANARY_ANSWER_TEXT_MUST_NOT_APPEAR"

    def _stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return GenerationResult(
            answered=True,
            answer=stub_answer,
            chunk_ids=[997, 998, 999],
        ).model_dump_json()

    with _client_with_generator(ephemeral_collection, sample_body_vectors, _stub) as tc:
        resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == REFUSAL_MESSAGE
    assert data["citations"] == []
    assert stub_answer not in resp.text, "Stub answer text must not appear anywhere in response"


def test_mixed_valid_invalid_ids_only_valid_returned(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
) -> None:
    """Stub cites [1, 999, 2] → only ids 1 and 2 survive; exactly 2 citations."""

    def _stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return GenerationResult(
            answered=True,
            answer="mixed answer",
            chunk_ids=[1, 999, 2],
        ).model_dump_json()

    with _client_with_generator(ephemeral_collection, sample_body_vectors, _stub) as tc:
        resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "mixed answer"
    assert len(data["citations"]) == 2


def test_answered_false_causes_refusal_regardless_of_chunk_ids(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
) -> None:
    """answered=False with non-empty chunk_ids and non-empty answer → fixed refusal.

    D24 regression case: a model signalling refusal semantics while attaching
    ids and answer text must not leak either into the response.
    """
    stub_answer = "SHOULD_NOT_APPEAR_IN_RESPONSE"

    def _stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return GenerationResult(
            answered=False,
            answer=stub_answer,
            chunk_ids=[1, 2],
        ).model_dump_json()

    with _client_with_generator(ephemeral_collection, sample_body_vectors, _stub) as tc:
        resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == REFUSAL_MESSAGE
    assert data["citations"] == []
    assert stub_answer not in resp.text


def test_answered_true_no_valid_ids_is_false_refusal(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """answered=True with chunk_ids=[] → refusal + warning log (the false-refusal case).

    The response shape is identical to other refusals; the warning log is the
    observable signal that distinguishes this branch from a normal refusal so
    that false-refusal rate can be tracked rather than suppressed.
    """

    def _stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return GenerationResult(
            answered=True,
            answer="answer that should not appear",
            chunk_ids=[],
        ).model_dump_json()

    with (
        caplog.at_level(logging.WARNING, logger="app.main"),
        _client_with_generator(ephemeral_collection, sample_body_vectors, _stub) as tc,
    ):
        resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == REFUSAL_MESSAGE
    assert data["citations"] == []
    assert "cite-or-refuse" in caplog.text
    assert "no valid chunk_ids survived validation" in caplog.text


def test_malformed_generator_output_causes_refusal(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
) -> None:
    """Unparseable model output → refusal; no partial answer is emitted."""

    def _stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return "not valid json {{{"

    with _client_with_generator(ephemeral_collection, sample_body_vectors, _stub) as tc:
        resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == REFUSAL_MESSAGE
    assert data["citations"] == []


def test_cite_or_refuse_refusal_shape_matches_gate_refusal(
    ephemeral_collection: chromadb.Collection,
    sample_body_vectors: dict[str, npt.NDArray[np.float32]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cite-or-refuse refusal and similarity-gate refusal have identical response shapes."""

    # Gate refusal: raise threshold so every query is refused before generation.
    def _get_col() -> chromadb.Collection:
        return ephemeral_collection

    def _get_gen() -> Generator:
        return stub_generate

    def _get_bv() -> dict[str, npt.NDArray[np.float32]]:
        return sample_body_vectors

    app.dependency_overrides[get_collection] = _get_col
    app.dependency_overrides[get_generator] = _get_gen
    app.dependency_overrides[get_body_vectors] = _get_bv
    monkeypatch.setattr(main_module, "SIMILARITY_THRESHOLD", 999.0)
    try:
        gate_resp = TestClient(app, raise_server_exceptions=True).post(
            "/ask", json={"question": "What is Doxicap used for?"}
        )
    finally:
        app.dependency_overrides.clear()
        monkeypatch.undo()

    # Cite-or-refuse refusal: stub returns answered=True but only invalid ids.
    def _invalid_stub(prompt: str, *, timeout: float = 30.0, temperature: float = 0.0) -> str:
        return GenerationResult(answered=True, answer="ignored", chunk_ids=[999]).model_dump_json()

    with _client_with_generator(ephemeral_collection, sample_body_vectors, _invalid_stub) as tc:
        cor_resp = tc.post("/ask", json={"question": "What is Doxicap used for?"})

    assert gate_resp.status_code == cor_resp.status_code == 200
    assert gate_resp.json() == cor_resp.json()
