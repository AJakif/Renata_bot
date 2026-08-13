# Leaflet Assistant

Grounded RAG service over Renata medicine leaflets. `POST /ask` → answer + citations, or an explicit refusal.

## Requirements

- Python 3.13+
- `pdftotext` (poppler) for ingestion
- Ollama 0.32+ with `qwen2.5:3b` pulled (`ollama pull qwen2.5:3b`), **or** a Groq API key

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # then edit .env to taste
```

## Running with Docker

> **Host runtime requirement.** The image bundles the FastAPI service, the
> embedding model (~167 MB), and all Python dependencies, but **not** a language
> model or model runtime. The LLM daemon (Ollama) must be installed and running
> on the **host machine** before starting the container. This is a deliberate
> trade-off documented in issue #12: keeping the model outside Docker's VM avoids
> memory-ceiling failures on 8 GB machines and keeps the model warm across
> container restarts.
>
> This is "runs on any machine **with the model runtime installed on the host**",
> not "runs on any machine". The Groq path (hosted API, `LLM_PROVIDER=groq`) is
> the zero-install alternative.

### Prerequisites

- Docker Desktop (Windows/Mac) or Docker Engine + Compose plugin (Linux)
- Ollama running on the host with `qwen2.5:3b` pulled (`ollama pull qwen2.5:3b`)
- The `docs/` directory containing the five leaflet PDFs (present in this repo)

### Start

```bash
docker compose up
```

The first run builds the image (~750 MB), which includes pre-warming the
embedding model. Subsequent starts reuse the cached image and only re-ingest
the PDFs (~10–20 s depending on hardware).

The API is available at `http://localhost:8000` and the UI at
`http://localhost:8000/`.

### Add a new leaflet (no rebuild)

```bash
# Drop the new PDF into docs/, then restart:
docker compose restart app
```

The lifespan re-ingests all PDFs in the mounted `./docs/` directory on every
container start. No image rebuild is needed.

### Switch to Groq

```bash
LLM_PROVIDER=groq GROQ_API_KEY=your-key docker compose up
```

Or edit `compose.yaml` to uncomment the `groq` env vars.

### Verify the host Ollama connection (Linux)

On Docker Desktop (Windows/Mac) `host.docker.internal` resolves automatically.
On Linux Docker Engine the `extra_hosts: host-gateway` entry in `compose.yaml`
maps the name — verify with:

```bash
docker compose exec app curl -s http://host.docker.internal:11434
# Expected: "Ollama is running"
```

### Memory budget with Docker (8 GB machine)

| Component | Resident |
|---|---|
| Docker Desktop VM overhead | ~500 MB |
| Container (Python + Chroma + embedding model) | ~500 MB |
| Host: `qwen2.5:3b` (Q4) in Ollama | ~2.1 GB |
| Host: OS | ~2–3 GB |
| **Total** | **~5–6 GB** |

---

## Running the server

### Local model (Ollama — default)

Start the Ollama daemon with memory-safe settings **before** starting the app:

```bash
# Daemon-level env vars: one KV-cache slot and one resident model.
# Without these, Ollama may auto-select 4 parallel slots (silent 4× RAM use)
# or load multiple models simultaneously.
OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

Then in a second terminal:

```bash
uvicorn app.main:app --reload
```

The service picks up `LLM_PROVIDER`, `LLM_MODEL`, `OLLAMA_HOST`, and `OLLAMA_KEEP_ALIVE` from `.env`
(defaults: `ollama`, `qwen2.5:3b`, `http://localhost:11434`, `5m`).

**Memory budget (8 GB machine):**

| Component | Resident |
|---|---|
| OS | ~2–3 GB |
| Service (Python + Chroma) | ~350 MB |
| `qwen2.5:3b` (Q4) | ~2.1 GB |
| **Total** | **~4.5–5.5 GB** |

Verified via `ollama ps` after the first request. `num_ctx=2048` is set explicitly
to avoid the default 8192-token context window, which would multiply KV-cache size
by 4 for no benefit (the prompt is ~700 tokens).

### Hosted model (Groq)

```bash
# In .env:
LLM_PROVIDER=groq
GROQ_API_KEY=your-key-here
LLM_MODEL=llama-3.1-8b-instant   # or any Groq chat model
```

```bash
uvicorn app.main:app --reload
```

No Ollama daemon required.

## Running retrieval and evaluation without an LLM

Ingestion, retrieval, and the eval harness do not import `app.main` or invoke
any LLM — they run with no model installed and no API key:

```bash
# Eval harness (Seam 2 — real embeddings, no LLM)
python scripts/eval.py

# Similarity measurement
python scripts/measure_similarity.py
```

## Running tests

```bash
pytest
```

Tests use `stub_generate` (no network calls). The Ollama/Groq adapters are
covered by mocked-httpx unit tests in `tests/test_generate.py`.

## Provider notes

- **No auto-detection, no fallback.** `LLM_PROVIDER` selects exactly one adapter per process.
  The same question must resolve identically on every machine with the same config.
- **Determinism.** `temperature=0` and `seed=42` are set on every call.
  Verified byte-identical across three consecutive runs on `qwen2.5:3b`.
- **Timeouts.** Every provider call carries an explicit `timeout` (default 30 s).
  A timeout degrades to a refusal response rather than a 500 error.
