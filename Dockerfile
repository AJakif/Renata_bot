# syntax=docker/dockerfile:1
FROM python:3.13-slim

WORKDIR /app

# libgomp1: OpenMP runtime required by onnxruntime (chromadb's embedding backend).
# Without it the ONNX model silently fails to initialise.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Dependency layer (re-built only when pyproject.toml changes) ──────────────
COPY pyproject.toml .
# Install runtime deps only; no dev extras, no editable install.
# Packages are wheel-only: no compiler toolchain needed in the final image.
RUN pip install --no-cache-dir \
    "fastapi>=0.139" \
    "uvicorn[standard]>=0.50" \
    "chromadb>=1.5" \
    "httpx>=0.28" \
    "pypdf>=5.0" \
    "python-dotenv>=1.0" \
    "rank-bm25>=0.2"

# ── Application code ──────────────────────────────────────────────────────────
COPY app/ app/
COPY static/ static/

# ── Bake the embedding model (~167 MB ONNX) into this image layer ─────────────
# chromadb's DefaultEmbeddingFunction downloads all-MiniLM-L6-v2 from Hugging
# Face on first call and caches it at /root/.cache/chroma/onnx_models/.
# Running it once here writes those files into the layer so that:
#   • no network access is required when the container starts, and
#   • the ~17 s cold-download is paid at build time, not at first request.
# docs/ is NOT needed here; the dummy string "warm" is enough to trigger the
# download.  The real corpus is provided by the ./docs volume at runtime.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['warm']); print('Embedding model cached at build time.')"

EXPOSE 8000

# python -m uvicorn adds /app (WORKDIR) to sys.path, making 'app.main' importable
# without a package install step.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
