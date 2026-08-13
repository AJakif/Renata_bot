"""LLM generation interface, real provider adapters, and stub implementation.

The Generator Protocol is the single injection point for the LLM provider.
Select the active adapter via LLM_PROVIDER env var ('ollama' | 'groq');
call make_generator() once from the application lifespan.

Fixed engineering constants (not env-configurable per D9):
  _NUM_CTX     = 2048  (prompt ~700 tokens + 300 output; larger wastes KV cache)
  _NUM_PREDICT = 300
  _SEED        = 42    (combined with temperature=0 for byte-identical determinism)
"""

import logging
import os
import re
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Fixed engineering constants — set here as named constants, not magic numbers,
# so they are visible to code review without being env-configurable.
_NUM_CTX: int = 2048
_NUM_PREDICT: int = 300
_SEED: int = 42


class GenerationResult(BaseModel):
    """Structured response expected from the LLM, parsed from its JSON output.

    answered: False means the model determined the context does not answer the
              question; the caller must ignore answer and chunk_ids entirely.
    chunk_ids: 1-based indices into the numbered context passages used.
    """

    answered: bool
    answer: str
    chunk_ids: list[int]


def parse_generation_result(raw: str) -> GenerationResult | None:
    """Parse a raw JSON string into a GenerationResult.

    Returns None on any parse or validation failure — callers treat None as
    "refuse without emitting a partial answer".  Never raises.
    """
    try:
        return GenerationResult.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None


# Matches the context-block prefix written by main.py: "[<int>] ("
_CONTEXT_ID_RE: re.Pattern[str] = re.compile(r"\[(\d+)\] \(")


@runtime_checkable
class Generator(Protocol):
    """Callable that takes a prompt and returns the model's raw JSON response."""

    def __call__(
        self,
        prompt: str,
        *,
        timeout: float = 30.0,
        temperature: float = 0.0,
    ) -> str:
        """Generate an answer for *prompt*.

        Args:
            prompt: The full prompt string sent to the model.
            timeout: Maximum seconds to wait for a response (enforced by real
                     adapters; ignored by the stub).
            temperature: Sampling temperature passed to the provider API;
                         0.0 = deterministic.  Ignored by the stub.

        Returns:
            The model's raw response text, expected to be a JSON string
            matching GenerationResult.  Parsing happens via
            parse_generation_result, not inside the Protocol.
        """
        ...  # pragma: no cover


class ProviderError(RuntimeError):
    """Raised by real LLM adapters on HTTP, timeout, or unexpected response shape.

    Caught by the /ask handler to return a refusal rather than a 500.
    """


class OllamaGenerator:
    """Generate via a local Ollama daemon (POST /api/generate).

    Each call opens its own httpx session (stateless, thread-safe).
    The daemon must be running before any call is made; no connectivity check
    occurs at construction time, satisfying the "no API key needed for
    ingestion/retrieval" acceptance criterion.

    Server-side daemon tuning (set in the environment before 'ollama serve',
    not by this client):
        OLLAMA_NUM_PARALLEL=1        — one KV-cache slot; prevents silent 4x RAM use
        OLLAMA_MAX_LOADED_MODELS=1   — one model resident at a time

    These are documented in README.md run instructions.
    """

    def __init__(self, host: str, model: str, keep_alive: str) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._keep_alive = keep_alive

    def __call__(
        self,
        prompt: str,
        *,
        timeout: float = 30.0,
        temperature: float = 0.0,
    ) -> str:
        """Send prompt to Ollama and return the raw response text.

        Raises:
            ProviderError: on timeout, HTTP error, or unexpected response shape.
        """
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "seed": _SEED,
                "num_ctx": _NUM_CTX,
                "num_predict": _NUM_PREDICT,
            },
            "keep_alive": self._keep_alive,
        }
        try:
            resp = httpx.post(
                f"{self._host}/api/generate",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            return str(resp.json()["response"])
        except httpx.TimeoutException as exc:
            logger.error("Ollama request timed out after %.1fs: %s", timeout, exc)
            raise ProviderError(f"Ollama timed out after {timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama returned HTTP %s: %s", exc.response.status_code, exc)
            raise ProviderError(f"Ollama HTTP error {exc.response.status_code}") from exc
        except KeyError as exc:
            logger.error("Ollama response missing expected field: %s", exc)
            raise ProviderError(f"Unexpected Ollama response shape: missing field {exc}") from exc


class GroqGenerator:
    """Generate via the Groq hosted API (OpenAI-compatible chat completions).

    Each call opens its own httpx session (stateless, thread-safe).
    No connectivity check at construction time.

    Endpoint: POST https://api.groq.com/openai/v1/chat/completions
    """

    _ENDPOINT: str = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def __call__(
        self,
        prompt: str,
        *,
        timeout: float = 30.0,
        temperature: float = 0.0,
    ) -> str:
        """Send prompt to Groq and return the raw response text.

        Raises:
            ProviderError: on timeout, HTTP error, or unexpected response shape.
        """
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "seed": _SEED,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = httpx.post(
                self._ENDPOINT,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"])
        except httpx.TimeoutException as exc:
            logger.error("Groq request timed out after %.1fs: %s", timeout, exc)
            raise ProviderError(f"Groq timed out after {timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Groq returned HTTP %s: %s", exc.response.status_code, exc)
            raise ProviderError(f"Groq HTTP error {exc.response.status_code}") from exc
        except (KeyError, IndexError) as exc:
            logger.error("Groq response missing expected field: %s", exc)
            raise ProviderError(f"Unexpected Groq response shape: {exc}") from exc


def make_generator() -> Generator:
    """Construct the active Generator from environment variables.

    Reads LLM_PROVIDER (default: 'ollama') and provider-specific vars.
    Called once from the application lifespan; extracted here for testability.

    Raises:
        ValueError: for an unknown provider name or missing required config
                    (GROQ_API_KEY when LLM_PROVIDER=groq).
    """
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "ollama":
        return OllamaGenerator(
            host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            model=os.getenv("LLM_MODEL", "qwen2.5:3b"),
            keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "5m"),
        )
    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY must be set in the environment when LLM_PROVIDER=groq")
        return GroqGenerator(
            api_key=api_key,
            model=os.getenv("LLM_MODEL", "llama-3.1-8b-instant"),
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Must be 'ollama' or 'groq'.")


def stub_generate(
    prompt: str,
    *,
    timeout: float = 30.0,
    temperature: float = 0.0,
) -> str:
    """Deterministic stub — returns a GenerationResult JSON string.

    Parses context-block IDs from the prompt (pattern ``[<n>] (``),
    returns answered=True citing all found IDs.  Used in tests and as the
    default when no LLM_PROVIDER is configured.

    The ``timeout`` and ``temperature`` parameters are accepted but not used.
    """
    _ = timeout
    _ = temperature
    chunk_ids = [int(m.group(1)) for m in _CONTEXT_ID_RE.finditer(prompt)]
    return GenerationResult(
        answered=True,
        answer="This information is provided for illustrative purposes only.",
        chunk_ids=chunk_ids,
    ).model_dump_json()
