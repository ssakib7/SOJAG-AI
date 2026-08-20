"""End-to-end pipeline test with a stubbed OpenAI-compatible LLM and stubbed
Graph/Sheet endpoints — the port of the old repo's _e2e_test.mjs idea.

Exercises the REAL path: signed webhook -> per-sender queue -> batching -> Team(route)
leader delegates to the sales member -> save_lead tool -> phone validated (the number
the customer TYPED wins) -> reply sent via the Graph stub -> lead ledger + outbox row
-> Sheet sink delivery. No real network, no API keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient


class StubHandler(BaseHTTPRequestHandler):
    """One server plays every external role: LLM, Google Sheet, Meta Graph."""

    server_version = "Stub/1.0"
    log: list[dict] = []

    def log_message(self, *args):  # silence request logging
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Graph profile lookups: conversations -> empty; user profile -> dead (like prod).
        if "/conversations" in self.path:
            self._json({"data": []})
        else:
            self._json({"error": {"message": "object does not exist", "code": 3}}, status=400)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path.endswith("/chat/completions"):
            self._json(self._llm_response(body))
            return
        if self.path.endswith("/sheet"):
            StubHandler.log.append({"kind": "sheet", "body": body})
            self._json({"ok": True})
            return
        # Graph send API (messages / sender actions).
        StubHandler.log.append({"kind": "graph", "path": self.path, "body": body})
        self._json({"message_id": "mid.stub"})

    def _llm_response(self, body: dict) -> dict:
        tool_names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        messages = body.get("messages") or []
        has_tool_result = any(m.get("role") == "tool" for m in messages)

        def completion(message: dict, finish: str) -> dict:
            return {
                "id": "cmpl-stub", "object": "chat.completion", "created": int(time.time()),
                "model": body.get("model", "stub"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        def tool_call(name: str, args: dict) -> dict:
            return completion({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}],
            }, "tool_calls")

        if "delegate_task_to_member" in tool_names and not has_tool_result:
            StubHandler.log.append({"kind": "llm", "role": "leader"})
            return tool_call("delegate_task_to_member",
                            {"member_id": "sales", "task": "Handle this sales turn."})
        if "save_lead" in tool_names and not has_tool_result:
            StubHandler.log.append({"kind": "llm", "role": "member-toolcall"})
            return tool_call("save_lead", {
                # The model "mangles" the number (drops a digit) — the pipeline must
                # prefer the digits the customer actually typed.
                "name": "Orko Rahman", "phone": "0171234567",
                "interest": "Bar Council কোর্স", "remarks": "দ্রুত ভর্তি হতে চান",
            })
        StubHandler.log.append({"kind": "llm", "role": "member-final", "has_tool_result": has_tool_result})
        return completion({"role": "assistant",
                           "content": "ধন্যবাদ স্যার! আমাদের প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"}, "stop")


@pytest.fixture(scope="module")
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def e2e_client(stub_server, monkeypatch):
    from app.agents import factory, specialists
    from app.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub-key")
    monkeypatch.setenv("OPENROUTER_API_BASE", f"{stub_server}/v1")
    monkeypatch.setenv("GOOGLE_SHEET_WEBAPP_URL", f"{stub_server}/sheet")
    monkeypatch.setenv("GRAPH_API_BASE", f"{stub_server}/graph")
    monkeypatch.setenv("REPLY_MIN_SECONDS", "0")
    monkeypatch.setenv("REPLY_MAX_SECONDS", "0")

    get_settings.cache_clear()
    factory.reset_for_tests()
    specialists.reset_for_tests()
    StubHandler.log.clear()

    from app.main import app

    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()
    factory.reset_for_tests()
    specialists.reset_for_tests()


def _sign(body: bytes) -> str:
    from app.config import get_settings

    return "sha256=" + hmac.new(get_settings().app_secret.encode(), body, hashlib.sha256).hexdigest()


def test_full_lead_capture_flow(e2e_client):
    payload = {"object": "page", "entry": [{"messaging": [{
        "sender": {"id": "cust-1"},
        "message": {"mid": "m-e2e-1", "text": "আমি ভর্তি হতে চাই। Orko Rahman 01712345678"},
    }]}]}
    body = json.dumps(payload).encode()
    res = e2e_client.post("/webhook", content=body, headers={
        "content-type": "application/json", "x-hub-signature-256": _sign(body),
    })
    assert res.status_code == 200

    # The pipeline runs in background asyncio tasks inside the app's event loop; the
    # reading beat alone is 0.8–2.5s. Poll until the reply lands on the Graph stub.
    deadline = time.time() + 60
    sent_texts = []
    while time.time() < deadline:
        sent_texts = [
            entry["body"].get("message", {}).get("text")
            for entry in StubHandler.log
            if entry["kind"] == "graph" and entry["body"].get("message")
        ]
        sheet_hits = [entry for entry in StubHandler.log if entry["kind"] == "sheet"]
        if sent_texts and sheet_hits:
            break
        time.sleep(0.25)

    assert sent_texts, f"no reply reached the Graph stub; log={StubHandler.log}"
    assert "ধন্যবাদ" in sent_texts[-1]

    roles = [entry["role"] for entry in StubHandler.log if entry["kind"] == "llm"]
    assert "leader" in roles, "team leader never ran"
    assert "member-toolcall" in roles, "sales member never called save_lead"

    # Sheet delivery: the customer's TYPED number must win over the model's mangled copy.
    sheet_hits = [entry for entry in StubHandler.log if entry["kind"] == "sheet"]
    assert sheet_hits, f"lead never reached the sheet sink; log={StubHandler.log}"
    lead = sheet_hits[0]["body"]
    assert lead["phone"] == "01712345678"
    assert lead["name"] == "Orko Rahman"
    assert lead["leadId"]

    # Ledger row persisted (log-before-network).
    async def _count():
        from sqlalchemy import select

        from app.db.engine import db_session, reset_engine_for_tests
        from app.db.models import LeadRow

        reset_engine_for_tests()
        async with db_session() as db:
            return len((await db.execute(select(LeadRow))).scalars().all())

    assert asyncio.run(_count()) >= 1
