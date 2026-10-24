from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from arceus_core.errors import (
    InferenceCancelledError,
    InferenceInputError,
    InferenceOutputError,
    InferenceUnavailableError,
)
from arceus_core.inference import (
    CancellationToken,
    InferenceRequest,
    LocalInference,
    OutputContract,
)
from arceus_core.inference_ollama import OllamaBackend


class _Handler(BaseHTTPRequestHandler):
    responses: list[dict[str, object]] = []

    def log_message(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        return

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        if not self.responses:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        resp = self.responses.pop(0)
        status = resp.get("status", 200)
        body = resp.get("body", "")
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"models": []}).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()


def _start_server() -> tuple[HTTPServer, Thread]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_ollama_request_construction_and_parsing() -> None:
    server, _ = _start_server()
    try:
        _Handler.responses = [
            {"body": {"message": {"content": json.dumps({"action": "search", "query": "x"})}}},
        ]
        backend = OllamaBackend(
            base_url=f"http://127.0.0.1:{server.server_port}",
            model="test-model",
            num_predict=64,
            num_ctx=2048,
            temperature=0.0,
        )
        inference = LocalInference(backend)
        contract = OutputContract(
            name="t",
            required_keys=("action", "query"),
            field_types={"action": "string", "query": "string"},
        )
        resp = inference.generate(InferenceRequest(prompt="hi", contract=contract))
        assert resp.parsed == {"action": "search", "query": "x"}
        assert resp.backend_id == "ollama"
        assert resp.model_id == "test-model"
        assert resp.is_test_backend is False
    finally:
        server.shutdown()


def test_ollama_output_json_schema_passthrough() -> None:
    """An output_json_schema request must be sent as the structured `format`."""

    class SchemaRecorder(_Handler):
        bodies: list[dict[str, object]] = []

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            SchemaRecorder.bodies.append(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"message": {"content": "ok"}}).encode("utf-8"))

    server = HTTPServer(("127.0.0.1", 0), SchemaRecorder)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        schema = {
            "type": "object",
            "required": ["action"],
            "properties": {"action": {"type": "string"}},
        }
        backend = OllamaBackend(
            base_url=f"http://127.0.0.1:{server.server_port}",
            model="test-model",
        )
        inference = LocalInference(backend)
        resp = inference.generate(InferenceRequest(prompt="hi", output_json_schema=schema))
        assert resp.text == "ok"
        assert len(SchemaRecorder.bodies) == 1
        fmt = SchemaRecorder.bodies[0].get("format")
        assert isinstance(fmt, dict)
        assert fmt["type"] == "object"
        assert fmt["required"] == ["action"]
    finally:
        SchemaRecorder.bodies.clear()
        server.shutdown()


def test_ollama_invalid_json() -> None:
    server, _ = _start_server()
    try:
        _Handler.responses = [{"body": {"message": {"content": "not json"}}}]
        backend = OllamaBackend(
            base_url=f"http://127.0.0.1:{server.server_port}",
            num_predict=64,
            num_ctx=2048,
        )
        inference = LocalInference(backend)
        contract = OutputContract(
            name="t", required_keys=("action",), field_types={"action": "string"}
        )
        with pytest.raises(InferenceOutputError):
            inference.generate(InferenceRequest(prompt="hi", contract=contract))
    finally:
        server.shutdown()


def test_ollama_model_missing() -> None:
    server, _ = _start_server()
    try:
        _Handler.responses = [{"body": {"error": "model 'missing' not found"}}]
        backend = OllamaBackend(
            base_url=f"http://127.0.0.1:{server.server_port}",
            num_predict=64,
            num_ctx=2048,
        )
        inference = LocalInference(backend)
        with pytest.raises(InferenceUnavailableError):
            inference.generate(InferenceRequest(prompt="hi"))
    finally:
        server.shutdown()


def test_ollama_malformed_server_response() -> None:
    server, _ = _start_server()
    try:
        _Handler.responses = [{"body": "not json"}]
        backend = OllamaBackend(
            base_url=f"http://127.0.0.1:{server.server_port}",
            num_predict=64,
            num_ctx=2048,
        )
        inference = LocalInference(backend)
        with pytest.raises(InferenceOutputError):
            inference.generate(InferenceRequest(prompt="hi"))
    finally:
        server.shutdown()


def test_ollama_unavailable() -> None:
    backend = OllamaBackend(
        base_url="http://127.0.0.1:59999",
        connect_timeout_seconds=1,
        num_predict=64,
        num_ctx=2048,
    )
    inference = LocalInference(backend)
    with pytest.raises(InferenceUnavailableError):
        inference.generate(InferenceRequest(prompt="hi"))


def test_ollama_loopback_enforcement() -> None:
    with pytest.raises(InferenceInputError):
        OllamaBackend(base_url="http://localhost:11434")
    with pytest.raises(InferenceInputError):
        OllamaBackend(base_url="http://192.168.1.1:11434")


def test_ollama_cancellation_prechecked() -> None:
    server, _ = _start_server()
    try:
        backend = OllamaBackend(
            base_url=f"http://127.0.0.1:{server.server_port}",
            num_predict=64,
            num_ctx=2048,
        )
        inference = LocalInference(backend)
        token = CancellationToken()
        token.cancel()
        with pytest.raises(InferenceCancelledError):
            inference.generate(InferenceRequest(prompt="hi", cancellation=token))
    finally:
        server.shutdown()


def test_ollama_payload_is_conservative_and_json_sorted() -> None:
    server, _ = _start_server()
    try:
        received: dict[str, object] = {}

        class _CaptureHandler(BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
                return

            def do_POST(self) -> None:
                if self.path != "/api/chat":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                received["payload"] = json.loads(self.rfile.read(length))
                encoded = json.dumps({"message": {"content": json.dumps({"ok": True})}}).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self) -> None:
                if self.path == "/api/tags":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"models": []}).encode("utf-8"))
                    return
                self.send_response(404)
                self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = OllamaBackend(
                base_url=f"http://127.0.0.1:{server.server_port}",
                model="test-model",
                num_predict=64,
                num_ctx=2048,
            )
            inference = LocalInference(backend)
            _ = inference.generate(InferenceRequest(prompt="hi"))
            payload = received["payload"]
            assert payload["model"] == "test-model"
            assert payload["stream"] is False
            assert payload["think"] is False
            assert payload["options"] == {
                "num_ctx": 2048,
                "num_predict": 64,
                "temperature": 0.0,
                "top_p": 1.0,
            }
            assert "kv_cache_type" not in json.dumps(payload)
            assert json.dumps(payload) == json.dumps(payload, sort_keys=True)
        finally:
            server.shutdown()
    finally:
        server.server_close()
