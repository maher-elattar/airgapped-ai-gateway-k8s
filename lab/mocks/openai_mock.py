"""Small OpenAI-compatible mock server for disposable gateway tests."""

from __future__ import annotations

import hashlib
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_PORT = 8000


def deterministic_embedding(seed: str, *, length: int = 8) -> list[float]:
    """Return a stable non-empty embedding vector for a request seed."""

    digest = hashlib.sha256(seed.encode()).digest()
    return [round(digest[index] / 255, 6) for index in range(length)]


def chat_payload(model: str) -> dict[str, Any]:
    """Return a minimal OpenAI chat-completions response."""

    return {
        "id": f"chatcmpl-mock-{model}",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"mock response from {model}",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 4,
            "completion_tokens": 4,
            "total_tokens": 8,
        },
    }


def embedding_payload(model: str, request_body: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal OpenAI embeddings response."""

    seed = json.dumps(request_body.get("input", ""), sort_keys=True)
    return {
        "object": "list",
        "model": model,
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": deterministic_embedding(f"{model}:{seed}"),
            }
        ],
        "usage": {
            "prompt_tokens": 3,
            "total_tokens": 3,
        },
    }


class MockOpenAIHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing the small endpoint subset used by the lab."""

    server_version = "airgap-openai-mock/1.0"

    def do_GET(self) -> None:
        """Serve a simple health endpoint."""

        if self.path == "/healthz":
            self._write_json({"status": "ok"})
            return
        self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Serve OpenAI-compatible chat or embedding responses."""

        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            body = json.loads(raw_body.decode())
        except json.JSONDecodeError:
            self._write_json({"error": "invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
            return

        model = os.getenv("MODEL_ID", "mock-model")
        kind = os.getenv("MOCK_KIND", "chat")
        if self.path == "/v1/chat/completions" and kind == "chat":
            self._write_json(chat_payload(model))
            return
        if self.path == "/v1/embeddings" and kind == "embedding":
            self._write_json(embedding_payload(model, body))
            return
        self._write_json({"error": "endpoint not served by this mock"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        """Keep container logs small and deterministic."""

    def _write_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, sort_keys=True).encode()
        self.send_response(int(status))
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    """Run the mock service."""

    port = int(os.getenv("PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer(("0.0.0.0", port), MockOpenAIHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
