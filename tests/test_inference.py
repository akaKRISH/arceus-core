from __future__ import annotations

import json
import threading

import pytest

from arceus_core.errors import (
    InferenceBackendError,
    InferenceCancelledError,
    InferenceInputError,
    InferenceOutputError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)
from arceus_core.inference import (
    CancellationToken,
    FakeInferenceBackend,
    InferenceRequest,
    LocalInference,
    OutputContract,
    unavailable_backend,
)

CONTRACT = OutputContract(
    name="tool_request",
    required_keys=("action", "query"),
    field_types={"action": "string", "query": "string"},
)


def test_successful_structured_call_reports_identity_and_metrics() -> None:
    backend = FakeInferenceBackend(responses=[json.dumps({"action": "search", "query": "main.py"})])
    inference = LocalInference(backend)
    response = inference.generate(InferenceRequest(prompt="find main.py", contract=CONTRACT))

    assert response.parsed == {"action": "search", "query": "main.py"}
    assert response.backend_id == "fake-test-only"
    assert response.model_id == "deterministic-fake-test-v1"
    assert response.is_test_backend is True
    assert response.contract_name == "tool_request"
    assert response.metrics.input_chars == len("find main.py")
    assert response.metrics.output_chars == len(response.text)
    assert response.metrics.wall_time_seconds >= 0.0
    assert inference.capabilities.offline is True


def test_fake_backend_is_marked_as_test_backend() -> None:
    inference = LocalInference(FakeInferenceBackend())
    assert inference.capabilities.is_test_backend is True
    assert inference.capabilities.offline is True
    assert inference.capabilities.backend_id == "fake-test-only"


def test_invalid_json_is_reported_as_output_error() -> None:
    inference = LocalInference(FakeInferenceBackend(responses=["not json"]))
    with pytest.raises(InferenceOutputError, match="not valid JSON"):
        inference.generate(InferenceRequest(prompt="x", contract=CONTRACT))


def test_contract_rejects_missing_extra_and_wrong_typed_keys() -> None:
    missing = LocalInference(FakeInferenceBackend(responses=[json.dumps({"action": "search"})]))
    with pytest.raises(InferenceOutputError, match="missing required"):
        missing.generate(InferenceRequest(prompt="x", contract=CONTRACT))

    extra = LocalInference(
        FakeInferenceBackend(
            responses=[json.dumps({"action": "search", "query": "x", "sudo": True})]
        )
    )
    with pytest.raises(InferenceOutputError, match="unexpected key"):
        extra.generate(InferenceRequest(prompt="x", contract=CONTRACT))

    wrong_type = LocalInference(
        FakeInferenceBackend(responses=[json.dumps({"action": "search", "query": 7})])
    )
    with pytest.raises(InferenceOutputError, match="must be string"):
        wrong_type.generate(InferenceRequest(prompt="x", contract=CONTRACT))


def test_timeout_is_enforced_even_if_backend_keeps_running() -> None:
    backend = FakeInferenceBackend(delay_seconds=0.25)
    inference = LocalInference(backend)
    with pytest.raises(InferenceTimeoutError, match="timeout"):
        inference.generate(InferenceRequest(prompt="slow", timeout_seconds=0.01))


def test_pre_cancelled_request_never_reaches_backend() -> None:
    backend = FakeInferenceBackend(responses=["unused"])
    inference = LocalInference(backend)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(InferenceCancelledError, match="cancelled"):
        inference.generate(InferenceRequest(prompt="cancel me", cancellation=token))
    assert backend._index == 0


def test_cancellation_during_backend_call_is_reported() -> None:
    backend = FakeInferenceBackend(delay_seconds=0.2)
    inference = LocalInference(backend)
    token = CancellationToken()
    timer = threading.Timer(0.01, token.cancel)
    timer.start()
    try:
        with pytest.raises(InferenceCancelledError, match="cancelled"):
            inference.generate(
                InferenceRequest(prompt="cancel during", timeout_seconds=1.0, cancellation=token)
            )
    finally:
        timer.cancel()


def test_unavailable_or_non_offline_backend_is_rejected() -> None:
    with pytest.raises(InferenceUnavailableError, match="not offline"):
        LocalInference(unavailable_backend())

    online_only = FakeInferenceBackend(offline=False)
    with pytest.raises(InferenceUnavailableError, match="not offline"):
        LocalInference(online_only)


def test_structured_contract_requires_backend_capability() -> None:
    backend = FakeInferenceBackend(structured_output=False)
    inference = LocalInference(backend)
    with pytest.raises(InferenceUnavailableError, match="structured output"):
        inference.generate(InferenceRequest(prompt="x", contract=CONTRACT))


def test_backend_exception_is_wrapped_with_context() -> None:
    backend = FakeInferenceBackend(fail_with=RuntimeError("simulated backend failure"))
    inference = LocalInference(backend)
    with pytest.raises(InferenceBackendError, match="simulated backend failure"):
        inference.generate(InferenceRequest(prompt="x"))


def test_input_and_timeout_limits_are_validated() -> None:
    inference = LocalInference(FakeInferenceBackend(), max_input_chars=5)
    with pytest.raises(InferenceInputError, match="limit"):
        inference.generate(InferenceRequest(prompt="123456"))

    with pytest.raises(InferenceInputError, match="timeout"):
        InferenceRequest(prompt="x", timeout_seconds=0)


def test_output_json_schema_request_is_validated() -> None:
    """A JSON-Schema request must be a non-empty mapping."""

    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    inference = LocalInference(FakeInferenceBackend())
    response = inference.generate(InferenceRequest(prompt="x", output_json_schema=schema))
    assert response.text == "ok"  # fake backend ignores the schema

    with pytest.raises(InferenceInputError, match="JSON-Schema"):
        InferenceRequest(prompt="x", output_json_schema="not-a-mapping")
    with pytest.raises(InferenceInputError, match="must not be empty"):
        InferenceRequest(prompt="x", output_json_schema={})


def test_contract_rejects_non_object_json() -> None:
    inference = LocalInference(FakeInferenceBackend(responses=["[]"]))
    with pytest.raises(InferenceOutputError, match="JSON object"):
        inference.generate(InferenceRequest(prompt="x", contract=CONTRACT))


def test_fake_backend_default_response_matches_empty_contract() -> None:
    contract = OutputContract(
        name="status",
        required_keys=("message",),
        field_types={"message": "string"},
    )
    inference = LocalInference(FakeInferenceBackend())
    response = inference.generate(InferenceRequest(prompt="status", contract=contract))
    assert response.parsed == {"message": ""}
