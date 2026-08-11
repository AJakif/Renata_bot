"""LLM generation interface and stub implementation.

The Generator Protocol is the single injection point for the LLM provider.
Real adapters (Groq, Ollama) are added in a later slice; this slice ships only
the stub.  The timeout parameter is wired here so the real-provider path
enforces it without an interface change.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Generator(Protocol):
    """Callable that takes a prompt and returns the model's answer."""

    def __call__(self, prompt: str, *, timeout: float = 30.0) -> str:
        """Generate an answer for *prompt*.

        Args:
            prompt: The full prompt string sent to the model.
            timeout: Maximum seconds to wait for a response (enforced by real
                     adapters; ignored by the stub).

        Returns:
            The model's response text.
        """
        ...  # pragma: no cover


def stub_generate(prompt: str, *, timeout: float = 30.0) -> str:
    """Deterministic stub — returns a fixed string regardless of prompt.

    Used in tests and as the default when no LLM_PROVIDER is configured.
    The ``timeout`` parameter is accepted but not used.
    """
    _ = timeout  # unused in stub; real adapters will enforce it
    return "This information is provided for illustrative purposes only."
