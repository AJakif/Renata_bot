"""Unit tests for generate.py — parse_generation_result, real adapters, factory.

No live network calls: Ollama and Groq adapters are tested by patching httpx.post.
Seam 1 (POST /ask with injected stub) is in test_api.py.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.generate import (
    GroqGenerator,
    OllamaGenerator,
    ProviderError,
    make_generator,
    parse_generation_result,
)

# -- parse_generation_result ---------------------------------------------------


def test_parse_valid_result() -> None:
    """Valid JSON with all required fields parses to a GenerationResult."""
    raw = '{"answered": true, "answer": "Take with food.", "chunk_ids": [1, 3]}'
    result = parse_generation_result(raw)
    assert result is not None
    assert result.answered is True
    assert result.answer == "Take with food."
    assert result.chunk_ids == [1, 3]


def test_parse_invalid_json_returns_none() -> None:
    """Malformed JSON returns None — never raises."""
    assert parse_generation_result("not valid json {{{") is None


def test_parse_missing_required_field_returns_none() -> None:
    """JSON that omits a required field (here: 'answered') returns None."""
    assert parse_generation_result('{"answer": "hello", "chunk_ids": []}') is None


# -- OllamaGenerator -----------------------------------------------------------


def test_ollama_generate_success() -> None:
    """OllamaGenerator sends correct payload and returns model response text."""
    expected_text = '{"answered": true, "answer": "Take with food.", "chunk_ids": [1]}'
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": expected_text}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        gen = OllamaGenerator(host="http://localhost:11434", model="qwen2.5:3b", keep_alive="5m")
        result = gen("some prompt", timeout=15.0, temperature=0.0)

    assert result == expected_text

    # Verify payload shape and required fixed params.
    _, call_kwargs = mock_post.call_args
    assert call_kwargs["timeout"] == 15.0
    payload = call_kwargs["json"]
    assert payload["model"] == "qwen2.5:3b"
    assert payload["stream"] is False
    opts = payload["options"]
    assert opts["num_ctx"] == 2048
    assert opts["num_predict"] == 300
    assert opts["seed"] == 42
    assert opts["temperature"] == 0.0
    assert payload["keep_alive"] == "5m"


def test_ollama_generate_timeout_raises_provider_error() -> None:
    """Timeout on the Ollama HTTP call is wrapped in ProviderError."""
    with patch(
        "httpx.post",
        side_effect=httpx.TimeoutException("timed out", request=MagicMock()),
    ):
        gen = OllamaGenerator(host="http://localhost:11434", model="qwen2.5:3b", keep_alive="5m")
        with pytest.raises(ProviderError, match="timed out"):
            gen("prompt")


def test_ollama_generate_connect_error_raises_provider_error() -> None:
    """Connection refused (daemon not running) is wrapped in ProviderError, not left to propagate.

    httpx.ConnectError is a sibling of TimeoutException under RequestError, not a
    subclass of it — this is the most likely real-world failure (host Ollama daemon
    down) and must degrade to the grounding contract's refusal shape, not a 500.
    """
    with patch(
        "httpx.post",
        side_effect=httpx.ConnectError("connection refused", request=MagicMock()),
    ):
        gen = OllamaGenerator(host="http://localhost:11434", model="qwen2.5:3b", keep_alive="5m")
        with pytest.raises(ProviderError):
            gen("prompt")


def test_ollama_generate_http_error_raises_provider_error() -> None:
    """Non-2xx HTTP response from Ollama is wrapped in ProviderError."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 503
    with patch(
        "httpx.post",
        side_effect=httpx.HTTPStatusError(
            "service unavailable", request=MagicMock(), response=mock_resp
        ),
    ):
        gen = OllamaGenerator(host="http://localhost:11434", model="qwen2.5:3b", keep_alive="5m")
        with pytest.raises(ProviderError, match="503"):
            gen("prompt")


# -- GroqGenerator -------------------------------------------------------------


def test_groq_generate_success() -> None:
    """GroqGenerator sends Bearer auth, correct payload, returns message content."""
    expected_text = '{"answered": true, "answer": "Swallow with water.", "chunk_ids": [2]}'
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": expected_text}}]}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        gen = GroqGenerator(api_key="test-key-123", model="llama-3.1-8b-instant")
        result = gen("some prompt", timeout=20.0, temperature=0.0)

    assert result == expected_text

    _, call_kwargs = mock_post.call_args
    assert call_kwargs["timeout"] == 20.0
    assert call_kwargs["headers"]["Authorization"] == "Bearer test-key-123"
    payload = call_kwargs["json"]
    assert payload["model"] == "llama-3.1-8b-instant"
    assert payload["messages"] == [{"role": "user", "content": "some prompt"}]
    assert payload["seed"] == 42
    assert payload["temperature"] == 0.0


def test_groq_generate_http_error_raises_provider_error() -> None:
    """Non-2xx HTTP response from Groq is wrapped in ProviderError."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    with patch(
        "httpx.post",
        side_effect=httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=mock_resp),
    ):
        gen = GroqGenerator(api_key="bad-key", model="llama-3.1-8b-instant")
        with pytest.raises(ProviderError, match="401"):
            gen("prompt")


def test_groq_generate_connect_error_raises_provider_error() -> None:
    """Connection failure to Groq is wrapped in ProviderError, not left to propagate."""
    with patch(
        "httpx.post",
        side_effect=httpx.ConnectError("connection refused", request=MagicMock()),
    ):
        gen = GroqGenerator(api_key="test-key-123", model="llama-3.1-8b-instant")
        with pytest.raises(ProviderError):
            gen("prompt")


# -- make_generator ------------------------------------------------------------


def test_make_generator_default_is_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_generator() returns OllamaGenerator when LLM_PROVIDER is unset."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    gen = make_generator()
    assert isinstance(gen, OllamaGenerator)


def test_make_generator_ollama_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """OllamaGenerator picks up custom host, model, and keep_alive from env."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://remote:11434")
    monkeypatch.setenv("LLM_MODEL", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "0")
    gen = make_generator()
    assert isinstance(gen, OllamaGenerator)
    assert gen._host == "http://remote:11434"
    assert gen._model == "llama3.2:3b"
    assert gen._keep_alive == "0"


def test_make_generator_ollama_reads_num_ctx_and_predict(monkeypatch: pytest.MonkeyPatch) -> None:
    """OLLAMA_NUM_CTX / OLLAMA_NUM_PREDICT override the measured defaults."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "4096")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "512")
    gen = make_generator()
    assert isinstance(gen, OllamaGenerator)
    assert gen._num_ctx == 4096
    assert gen._num_predict == 512


def test_make_generator_groq_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_generator() raises ValueError when provider=groq and key is absent."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        make_generator()


def test_make_generator_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_generator() raises ValueError for an unrecognised provider name."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="openai"):
        make_generator()
