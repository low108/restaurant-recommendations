"""Exercise the real HTTP adapter against a local fixture, never live ILMU."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dining.llm.inference import InferenceSettings, invoke_explanations


@pytest.fixture
def local_provider(monkeypatch):
    """Record only synthetic requests; suppress server/access header logging."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    state = {"requests": [], "status": 200, "response": {}}
    synthetic_key = "synthetic-local-protocol-test-key"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state["requests"].append(
                {
                    "path": self.path,
                    "authenticated": self.headers.get("Authorization")
                    == f"Bearer {synthetic_key}",
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            payload = json.dumps(state["response"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    settings = InferenceSettings(
        provider="ilmu",
        model="ilmu-mini-v3.3",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key=synthetic_key,
        timeout_seconds=2,
        max_output_tokens=256,
    )
    try:
        yield settings, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_real_chat_transport_sends_bounded_label_request_and_reads_usage(
    local_provider,
):
    settings, state = local_provider
    labels = {
        "probe-option-a": ["group_fit", "budget"],
        "probe-option-b": ["nearby", "budget"],
    }
    state["response"] = {
        "id": "chatcmpl-local-protocol-test",
        "object": "chat.completion",
        "created": 1,
        "model": "ilmu-mini-v3.3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(labels)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 102, "completion_tokens": 31, "total_tokens": 133},
    }

    result = invoke_explanations(settings, list(labels))

    assert len(state["requests"]) == 1
    request = state["requests"][0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authenticated"] is True
    body = request["body"]
    assert body["model"] == "ilmu-mini-v3.3"
    assert body.get("max_completion_tokens", body.get("max_tokens")) == 256
    assert body["temperature"] == 0
    assert body.get("stream", False) is False
    assert "tools" not in body
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert json.loads(body["messages"][1]["content"]) == {
        "options": [
            {
                "id": option_id,
                "allowed_reason_ids": ["group_fit", "budget", "nearby"],
            }
            for option_id in labels
        ]
    }
    assert result.labels == labels
    assert result.metadata["inference_status"] == "validated"
    assert result.metadata["model_calls"] == 1
    assert result.metadata["returned_model_id"] == "ilmu-mini-v3.3"
    assert result.metadata["usage_source"] == "provider"
    assert result.metadata["usage"] == {
        "input_tokens": 102,
        "output_tokens": 31,
        "total_tokens": 133,
    }
    assert result.metadata["input_tokens"] == 102
    assert result.metadata["output_tokens"] == 31
    assert settings.api_key not in repr(result)


@pytest.mark.parametrize("http_status", [429, 500])
def test_http_error_is_not_retried_and_does_not_expose_provider_body(
    local_provider, http_status
):
    settings, state = local_provider
    private_marker = "fixture-private-provider-error-detail"
    state["status"] = http_status
    state["response"] = {
        "error": {
            "message": private_marker,
            "type": "rate_limit_error" if http_status == 429 else "server_error",
            "code": "fixture_failure",
        }
    }

    result = invoke_explanations(settings, ["probe-option-a"])

    assert len(state["requests"]) == 1
    assert result.labels is None
    assert result.metadata["model_calls"] == 1
    assert result.metadata["model_status"] == "template_fallback"
    assert result.metadata["inference_status"] == "provider_error"
    assert result.metadata["model_error_code"] == "provider_error"
    assert result.metadata["input_tokens"] is None
    assert result.metadata["output_tokens"] is None
    assert result.metadata["usage_source"] == "unknown"
    assert private_marker not in repr(result)
    assert settings.api_key not in repr(result)
    assert settings.base_url not in repr(result)
