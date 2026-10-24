"""Ollama HTTP adapter for local inference.

This adapter talks only to http://127.0.0.1:11434 (loopback). It uses the
standard library and is compatible with the existing InferenceBackend
interface. It does not send filesystem contents, credentials, or personal
data.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from arceus_core.errors import (
    InferenceBackendError,
    InferenceCancelledError,
    InferenceInputError,
    InferenceOutputError,
    InferenceUnavailableError,
)
from arceus_core.inference import (
    DEFAULT_MAX_INPUT_CHARS,
    DEFAULT_MAX_OUTPUT_CHARS,
    BackendCapabilities,
    InferenceRequest,
)


@dataclass(slots=True)
class OllamaBackend:
    """Ollama chat backend at loopback."""

    model: str = "qwen3.5:4b"
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 5.0
    num_predict: int = 64
    num_ctx: int = 2048
    temperature: float = 0.0
    top_p: float = 1.0

    def __post_init__(self) -> None:
        if not self.model:
            raise InferenceInputError("model must not be empty")
        if not self.base_url.startswith("http://127.0.0.1:"):
            raise InferenceInputError("base_url must be loopback (http://127.0.0.1:...)")
        if self.timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise InferenceInputError("timeouts must be positive")
        if self.num_predict <= 0 or self.num_ctx <= 0:
            raise InferenceInputError("num_predict/num_ctx must be positive")

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id="ollama",
            model_id=self.model,
            offline=True,
            structured_output=True,
            max_input_chars=DEFAULT_MAX_INPUT_CHARS,
            max_output_chars=DEFAULT_MAX_OUTPUT_CHARS,
            supports_cancellation=True,
            supports_gpu=True,
            is_test_backend=False,
        )

    def generate(self, request: InferenceRequest) -> str:
        if request.cancellation is not None and request.cancellation.cancelled:
            raise InferenceCancelledError("inference request was cancelled")

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        options: dict[str, Any] = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "num_predict": self.num_predict,
            "num_ctx": self.num_ctx,
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
        }
        if options:
            payload["options"] = options

        if request.output_json_schema is not None:
            # Structured-output mode: constrain decoding with a JSON Schema.
            # The schema guides the decoder only; application validation runs
            # afterwards regardless of what the decoder produced.
            payload["format"] = dict(request.output_json_schema)
        elif request.contract is not None:
            # Ask for JSON output; we still validate strictly
            payload["format"] = "json"

        # The adapter never sends browser-only sampler knobs such as
        # top_k or mirostat. Those belong to the Ollama UI layer and are
        # rejected by llama-server with "invalid option provided".
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read()
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise InferenceUnavailableError(f"ollama unavailable (timeout): {reason}") from exc
            if isinstance(reason, OSError):
                raise InferenceUnavailableError(f"ollama unavailable: {reason}") from exc
            msg = str(reason).lower()
            if "timed out" in msg or "timeout" in msg:
                raise InferenceUnavailableError(f"ollama unavailable (timeout): {reason}") from exc
            raise InferenceUnavailableError(f"ollama unavailable: {reason}") from exc
        except TimeoutError as exc:
            raise InferenceUnavailableError(f"ollama unavailable (timeout): {exc}") from exc

        try:
            result = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise InferenceOutputError(f"ollama returned invalid JSON: {exc.msg}") from exc

        if isinstance(result, dict) and result.get("error"):
            err = str(result["error"])
            if "not found" in err.lower() or "does not exist" in err.lower():
                raise InferenceUnavailableError(f"ollama model missing: {err}")
            raise InferenceBackendError(f"ollama error: {err}")

        message = result.get("message") if isinstance(result, dict) else None
        if isinstance(message, dict):
            content = message.get("content", "")
        elif isinstance(result, dict):
            content = result.get("response", "") or result.get("content", "")
        else:
            content = ""

        if not isinstance(content, str):
            raise InferenceOutputError("ollama returned non-string content")

        return content

    def list_models(self) -> list[dict[str, Any]]:
        """List models from Ollama (for diagnostics)."""
        req = urllib.request.Request(
            f"{self.base_url}/api/tags",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.connect_timeout_seconds) as resp:
                body = resp.read()
        except urllib.error.URLError as exc:
            raise InferenceUnavailableError(f"ollama unavailable: {exc.reason}") from exc
        try:
            result = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise InferenceOutputError(f"ollama tags invalid JSON: {exc.msg}") from exc
        if isinstance(result, dict):
            models = result.get("models", [])
            if isinstance(models, list):
                return models
        return []
