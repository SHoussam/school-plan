#!/usr/bin/env python3
"""Offline unit tests for local_ai.py using a threaded fake LM Studio HTTP server."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import sys
import threading
import time

import pytest

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan import config
from school_plan import local_ai


@contextmanager
def fake_lms_server(handler_fn):
    """Spins up a local HTTP server in a daemon thread and yields (base_url, recorded_requests)."""
    recorded = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            recorded.append({"method": "GET", "path": self.path, "body": None})
            status, payload = handler_fn("GET", self.path, None, len(recorded))
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            recorded.append({"method": "POST", "path": self.path, "body": parsed})
            status, payload = handler_fn("POST", self.path, parsed, len(recorded))
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", recorded
    finally:
        server.shutdown()
        server.server_close()


def _valid_ai_content(**overrides):
    obj = {
        "is_actionable": True,
        "title": "Titre Réécrit Par Gemma",
        "module": "NoSQL",
        "teacher": "Houda Zaim",
        "task_type": "COMPTE_RENDU",
        "deadline_text": "2026-11-21 23:59",
        "priority": "HIGH",
        "summary": "Résumé clair.",
        "confidence": 0.92,
    }
    obj.update(overrides)
    return json.dumps(obj, ensure_ascii=False)


def test_outgoing_payload_uses_reasoning_effort_none_and_json_schema(monkeypatch):
    def handler(method, path, body, count):
        if path == "/api/v0/models":
            return 200, {"data": [{"id": config.LMS_MODEL, "state": "loaded"}]}
        return 200, {
            "choices": [{"finish_reason": "stop", "message": {"content": _valid_ai_content()}}],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 0}},
        }

    with fake_lms_server(handler) as (base_url, recorded):
        monkeypatch.setattr(config, "LMS_BASE", base_url)
        res = local_ai.extract_one("Compte rendu NoSQL prof Houda Zaim")
        assert res is not None
        assert res["module"] == "NoSQL"

        posts = [r["body"] for r in recorded if r["method"] == "POST"]
        assert len(posts) == 1
        sent = posts[0]
        assert sent["reasoning_effort"] == "none"
        assert sent["response_format"]["type"] == "json_schema"
        assert sent["response_format"]["json_schema"]["strict"] is True
        assert "enableThinking" not in sent
        assert "chat_template_kwargs" not in sent


def test_reasoning_only_response_is_rejected_and_heuristic_kept(monkeypatch):
    def handler(method, path, body, count):
        if path == "/api/v0/models":
            return 200, {"data": [{"id": config.LMS_MODEL, "state": "loaded"}]}
        return 200, {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": '[{"title": "Fake thinking JSON", "module": "NoSQL"}]',
                    },
                }
            ],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 300}},
        }

    with fake_lms_server(handler) as (base_url, _recorded):
        monkeypatch.setattr(config, "LMS_BASE", base_url)
        candidates = [{"title": "Mon TP", "module": "Divers", "teacher": "", "source": "Outlook"}]
        out, stats = local_ai.enrich(candidates, budget_s=10.0)
        assert stats["sent"] == 1
        assert stats["failed"] == 1
        assert stats["ok"] == 0
        assert out[0]["module"] == "Divers"
        assert "ai" not in out[0]


def test_unloaded_error_triggers_single_retry_after_ensure_loaded(monkeypatch):
    post_calls = []

    def handler(method, path, body, count):
        if path == "/api/v0/models":
            return 200, {"data": [{"id": config.LMS_MODEL, "state": "loaded"}]}
        post_calls.append(count)
        if len(post_calls) == 1:
            return 200, {"error": "Model unloaded by user or API request."}
        return 200, {
            "choices": [{"finish_reason": "stop", "message": {"content": _valid_ai_content(module="Dotnet")}}],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 0}},
        }

    load_called = []
    monkeypatch.setattr(local_ai, "ensure_loaded", lambda allow_load=True: (load_called.append(allow_load) or True))

    with fake_lms_server(handler) as (base_url, _recorded):
        monkeypatch.setattr(config, "LMS_BASE", base_url)
        res = local_ai.extract_one("Serie 2 C# Dotnet")
        assert res is not None
        assert res["module"] == "Dotnet"
        assert len(post_calls) == 2
        assert load_called == [True]


def test_rewritten_title_still_merges_into_correct_candidate_by_index(monkeypatch):
    """Regression test for bug AI-3: AI rewrites title, but result must still merge by candidate index."""
    def handler(method, path, body, count):
        if path == "/api/v0/models":
            return 200, {"data": [{"id": config.LMS_MODEL, "state": "loaded"}]}
        return 200, {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": _valid_ai_content(
                            title="Completely Different Rewritten Title",
                            module="Machine Learning",
                            teacher="Soundouss Abroun",
                            deadline_text="2026-11-22 18:00",
                            confidence=0.95,
                        )
                    },
                }
            ],
        }

    with fake_lms_server(handler) as (base_url, _recorded):
        monkeypatch.setattr(config, "LMS_BASE", base_url)
        candidates = [
            {
                "title": "Original Raw Email Subject Line",
                "module": "Divers",
                "teacher": "",
                "deadline": "Séance prochaine",
                "due_text": "",
                "end": "",
                "priority": "MEDIUM",
                "source": "Outlook",
            }
        ]
        out, stats = local_ai.enrich(candidates, budget_s=10.0)
        assert stats["ok"] == 1
        assert out[0]["title"] == "Original Raw Email Subject Line"
        assert out[0]["module"] == "Machine Learning"
        assert out[0]["teacher"] == "Soundouss Abroun"
        assert out[0]["end"] == "20261122T180000Z"
        assert "module" in out[0]["ai"]["fields"]
        assert "teacher" in out[0]["ai"]["fields"]
        assert "deadline" in out[0]["ai"]["fields"]


def test_low_confidence_module_does_not_override_heuristic_and_budget_skips(monkeypatch):
    def handler(method, path, body, count):
        if path == "/api/v0/models":
            return 200, {"data": [{"id": config.LMS_MODEL, "state": "loaded"}]}
        time.sleep(0.25)
        return 200, {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": _valid_ai_content(
                            module="NoSQL",
                            confidence=0.45,  # below 0.7 threshold
                        )
                    },
                }
            ],
        }

    with fake_lms_server(handler) as (base_url, _recorded):
        monkeypatch.setattr(config, "LMS_BASE", base_url)
        candidates = [
            {"title": "Tâche 1 Virtualisation", "module": "Virtualisation", "teacher": "Imane Elabid", "source": "Google Classroom"},
            {"title": "Tâche 2", "module": "Divers", "teacher": "", "source": "Outlook"},
            {"title": "Tâche 3", "module": "Divers", "teacher": "", "source": "Outlook"},
        ]
        out, stats = local_ai.enrich(candidates, budget_s=0.30)
        # First item processed, low confidence NoSQL must NOT overwrite Virtualisation
        assert out[0]["module"] == "Virtualisation"
        assert stats["sent"] == 1
        assert stats["skipped"] == 2
