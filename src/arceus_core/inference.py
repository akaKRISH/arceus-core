"""Optional local language-model inference boundary.

This module defines the smallest replaceable interface needed before a real
local model is selected. It is deliberately independent of the CLI,
filesystem index, graph, and database services. No backend is registered or
loaded implicitly; the application remains fully functional when inference is
disabled or unavailable.

Backends must be local/offline and must report their model identity and
capabilities. Output is validated against an explicit structured contract
before it can be used by callers. The deterministic fake backend in this
module is for tests only and is never presented as a real language model.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from arceus_core.errors import (
    InferenceBackendError,
    InferenceCancelledError,
    InferenceError,
    InferenceInputError,
    InferenceOutputError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_INPUT_CHARS = 32_768
DEFAULT_MAX_OUTPUT_CHARS = 8_192


class CancellationToken:
    """Thread-safe cooperative cancellation signal for local inference."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise InferenceCancelledError("inference request was cancelled")


@dataclass(frozen=True, slots=True)
class OutputContract:
    """Required shape for structured model output.

    ``field_types`` uses simple names: ``string``, ``integer``, ``number``,
    ``boolean``, ``object``, or ``array``. Unknown fields are rejected by
    default so untrusted model output cannot smuggle extra capabilities.
    """

    name: str
    required_keys: tuple[str, ...]
    field_types: Mapping[str, str] = field(default_factory=dict)
    allow_extra_keys: bool = False

    def validate(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise InferenceOutputError(f"{self.name}: output must be a JSON object")

        missing = [key for key in self.required_keys if key not in payload]
        if missing:
            raise InferenceOutputError(
                f"{self.name}: missing required key(s): {', '.join(missing)}"
            )
        if not self.allow_extra_keys:
            extras = sorted(set(payload) - set(self.required_keys) - set(self.field_types))
            if extras:
                raise InferenceOutputError(f"{self.name}: unexpected key(s): {', '.join(extras)}")

        for key, expected in self.field_types.items():
            if key not in payload:
                continue
            _validate_field_type(self.name, key, payload[key], expected)
        return dict(payload)


def _validate_field_type(contract_name: str, key: str, value: Any, expected: str) -> None:
    expected_name = expected.lower()
    if expected_name in {"any", "*"}:
        return
    if expected_name == "string":
        valid = isinstance(value, str)
    elif expected_name == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected_name == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_name == "boolean":
        valid = isinstance(value, bool)
    elif expected_name == "object":
        valid = isinstance(value, dict)
    elif expected_name == "array":
        valid = isinstance(value, list)
    else:
        raise InferenceOutputError(
            f"{contract_name}: contract uses unsupported type {expected!r} for {key!r}"
        )
    if not valid:
        raise InferenceOutputError(
            f"{contract_name}: key {key!r} must be {expected_name}, got {type(value).__name__}"
        )


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    backend_id: str
    model_id: str
    offline: bool
    structured_output: bool
    max_input_chars: int
    max_output_chars: int
    supports_cancellation: bool = True
    supports_gpu: bool = False
    is_test_backend: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "model_id": self.model_id,
            "offline": self.offline,
            "structured_output": self.structured_output,
            "max_input_chars": self.max_input_chars,
            "max_output_chars": self.max_output_chars,
            "supports_cancellation": self.supports_cancellation,
            "supports_gpu": self.supports_gpu,
            "is_test_backend": self.is_test_backend,
        }


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    prompt: str
    system_prompt: str = ""
    contract: OutputContract | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    cancellation: CancellationToken | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    output_json_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise InferenceInputError("prompt must not be empty")
        if self.timeout_seconds <= 0:
            raise InferenceInputError("timeout_seconds must be positive")
        if self.output_json_schema is not None:
            if not isinstance(self.output_json_schema, Mapping):
                raise InferenceInputError("output_json_schema must be a JSON-Schema mapping")
            if not self.output_json_schema:
                raise InferenceInputError("output_json_schema must not be empty")


@dataclass(frozen=True, slots=True)
class InferenceMetrics:
    wall_time_seconds: float
    backend_time_seconds: float
    input_chars: int
    output_chars: int
    peak_memory_mb: float | None = None
    gpu_memory_mb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "wall_time_seconds": self.wall_time_seconds,
            "backend_time_seconds": self.backend_time_seconds,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "peak_memory_mb": self.peak_memory_mb,
            "gpu_memory_mb": self.gpu_memory_mb,
        }


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    text: str
    parsed: dict[str, Any] | None
    backend_id: str
    model_id: str
    contract_name: str | None
    metrics: InferenceMetrics
    is_test_backend: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "parsed": self.parsed,
            "backend_id": self.backend_id,
            "model_id": self.model_id,
            "contract_name": self.contract_name,
            "metrics": self.metrics.to_dict(),
            "is_test_backend": self.is_test_backend,
        }


@runtime_checkable
class InferenceBackend(Protocol):
    """Replaceable local inference backend contract."""

    def capabilities(self) -> BackendCapabilities: ...

    def generate(self, request: InferenceRequest) -> str: ...


class LocalInference:
    """Synchronous, offline-only wrapper around one local inference backend."""

    def __init__(
        self,
        backend: InferenceBackend,
        *,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if max_input_chars < 1 or max_output_chars < 1:
            raise InferenceInputError("inference limits must be positive")
        if default_timeout_seconds <= 0:
            raise InferenceInputError("default timeout must be positive")
        self._backend = backend
        self._max_input_chars = max_input_chars
        self._max_output_chars = max_output_chars
        self._default_timeout_seconds = default_timeout_seconds
        self._capabilities = backend.capabilities()
        if not self._capabilities.offline:
            raise InferenceUnavailableError(
                f"backend {self._capabilities.backend_id!r} is not offline/local"
            )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def generate(self, request: InferenceRequest | None = None) -> InferenceResponse:
        if request is None:
            request = InferenceRequest(prompt="ping", timeout_seconds=self._default_timeout_seconds)
        if not isinstance(request, InferenceRequest):
            raise InferenceInputError("request must be an InferenceRequest")

        if not self._capabilities.structured_output and request.contract is not None:
            raise InferenceUnavailableError(
                f"backend {self._capabilities.backend_id!r} does not support structured output"
            )
        if not self._capabilities.supports_cancellation and request.cancellation is not None:
            raise InferenceUnavailableError(
                f"backend {self._capabilities.backend_id!r} does not support cancellation"
            )

        input_chars = len(request.prompt) + len(request.system_prompt)
        if input_chars > self._max_input_chars:
            raise InferenceInputError(
                f"request has {input_chars} characters; limit is {self._max_input_chars}"
            )

        cancellation = request.cancellation or CancellationToken()
        cancellation.raise_if_cancelled()
        started = time.perf_counter()
        text = self._call_backend(request, cancellation)
        wall_time = time.perf_counter() - started
        cancellation.raise_if_cancelled()

        if len(text) > self._max_output_chars:
            raise InferenceOutputError(
                f"backend returned {len(text)} characters; limit is {self._max_output_chars}"
            )

        parsed: dict[str, Any] | None = None
        contract_name: str | None = None
        if request.contract is not None:
            parsed = _parse_json_object(request.contract.name, text)
            parsed = request.contract.validate(parsed)
            contract_name = request.contract.name

        metrics = InferenceMetrics(
            wall_time_seconds=round(wall_time, 6),
            backend_time_seconds=round(wall_time, 6),
            input_chars=input_chars,
            output_chars=len(text),
            peak_memory_mb=None,
            gpu_memory_mb=None,
        )
        return InferenceResponse(
            text=text,
            parsed=parsed,
            backend_id=self._capabilities.backend_id,
            model_id=self._capabilities.model_id,
            contract_name=contract_name,
            metrics=metrics,
            is_test_backend=self._capabilities.is_test_backend,
        )

    def _call_backend(self, request: InferenceRequest, cancellation: CancellationToken) -> str:
        # A worker thread makes the timeout boundary explicit even for a
        # synchronous backend. The daemon thread cannot block process exit.
        result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result.put((True, self._backend.generate(request)))
            except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
                result.put((False, exc))

        thread = threading.Thread(target=worker, name="arceus-inference", daemon=True)
        thread.start()
        try:
            ok, value = result.get(timeout=request.timeout_seconds)
        except queue.Empty as exc:
            cancellation.cancel()
            raise InferenceTimeoutError(
                f"backend exceeded timeout of {request.timeout_seconds:.3f}s"
            ) from exc

        if not ok:
            if isinstance(value, InferenceError):
                raise value
            if isinstance(value, Exception):
                raise InferenceBackendError(
                    f"backend {self._capabilities.backend_id!r} failed: "
                    f"{type(value).__name__}: {value}"
                ) from value
            raise InferenceBackendError("backend failed with a non-exception value")
        return str(value)


def _parse_json_object(contract_name: str, text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InferenceOutputError(f"{contract_name}: output is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise InferenceOutputError(f"{contract_name}: output must decode to a JSON object")
    return payload


@dataclass(slots=True)
class FakeInferenceBackend:
    """Deterministic test double. This is not a real language model.

    The backend is explicitly marked ``is_test_backend`` and is suitable only
    for unit tests and offline development. It never performs network calls.
    """

    responses: Sequence[str] = ()
    delay_seconds: float = 0.0
    fail_with: Exception | None = None
    offline: bool = True
    structured_output: bool = True
    supports_cancellation: bool = True
    supports_gpu: bool = False
    _index: int = 0

    backend_id: Literal["fake-test-only"] = "fake-test-only"
    model_id: Literal["deterministic-fake-test-v1"] = "deterministic-fake-test-v1"

    def __post_init__(self) -> None:
        if self.delay_seconds < 0:
            raise InferenceInputError("delay_seconds must not be negative")

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id=self.backend_id,
            model_id=self.model_id,
            offline=self.offline,
            structured_output=self.structured_output,
            max_input_chars=DEFAULT_MAX_INPUT_CHARS,
            max_output_chars=DEFAULT_MAX_OUTPUT_CHARS,
            supports_cancellation=self.supports_cancellation,
            supports_gpu=self.supports_gpu,
            is_test_backend=True,
        )

    def generate(self, request: InferenceRequest) -> str:
        if request.cancellation is not None:
            request.cancellation.raise_if_cancelled()
        if self.fail_with is not None:
            raise self.fail_with
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if request.cancellation is not None:
            request.cancellation.raise_if_cancelled()

        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        if request.contract is not None:
            return json.dumps({key: _default_value(key) for key in request.contract.required_keys})
        return "ok"


def _default_value(key: str) -> Any:
    if key in {"success", "allowed", "decline", "requires_more_information"}:
        return False
    if key in {"items", "paths", "tools"}:
        return []
    if key in {"confidence", "reason", "message", "action"}:
        return ""
    return None


def unavailable_backend() -> InferenceBackend:
    """Return a backend object that reports itself as unavailable/offline=False."""

    class _Unavailable:
        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(
                backend_id="unavailable-test-backend",
                model_id="unavailable",
                offline=False,
                structured_output=False,
                max_input_chars=0,
                max_output_chars=0,
                supports_cancellation=False,
            )

        def generate(self, request: InferenceRequest) -> str:
            raise InferenceUnavailableError("backend is unavailable")

    return _Unavailable()


__all__ = [
    "BackendCapabilities",
    "CancellationToken",
    "DEFAULT_MAX_INPUT_CHARS",
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT_SECONDS",
    "FakeInferenceBackend",
    "InferenceBackend",
    "InferenceMetrics",
    "InferenceRequest",
    "InferenceResponse",
    "LocalInference",
    "OutputContract",
    "unavailable_backend",
]
