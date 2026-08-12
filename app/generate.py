"""LLM generation interface and stub implementation.

The Generator Protocol is the single injection point for the LLM provider.
Real adapters (Groq, Ollama) are added in a later slice; this slice ships only
the stub.  The timeout and temperature parameters are wired here so the
real-provider path enforces them without an interface change.
"""

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError


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
