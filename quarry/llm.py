"""Chat clients. The agent only needs `chat(messages, tools) -> message dict`."""
from __future__ import annotations

import json
import os
import re
from typing import Protocol

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("QUARRY_MODEL", "qwen2.5:7b")


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict]) -> dict: ...


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, model: str = DEFAULT_MODEL, url: str = OLLAMA_URL, timeout: float = 180.0,
                 num_ctx: int = int(os.environ.get("QUARRY_NUM_CTX", "8192"))):
        self.model = model
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self._http = httpx.Client(timeout=timeout)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {
            "model": self.model, "messages": messages, "tools": tools, "stream": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "seed": 7},
        }
        if self.model.startswith("qwen3"):
            body["think"] = False
        try:
            r = self._http.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from None
        data = r.json()
        msg = data.get("message") or {}
        msg["content"] = _THINK.sub("", msg.get("content") or "").strip()
        msg["_usage"] = {"prompt_tokens": data.get("prompt_eval_count", 0),
                         "completion_tokens": data.get("eval_count", 0),
                         "ms": int((data.get("total_duration") or 0) / 1e6)}
        return msg

    def models(self) -> list[str]:
        try:
            r = self._http.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        except httpx.HTTPError:
            return []


_THINK = re.compile(r"<think>.*?(</think>|$)", re.S)
_JSON_CALL =re.compile(r"\{[^{}]*\"name\"\s*:\s*\"(\w+)\"[^{}]*\"(?:arguments|parameters)\"\s*:\s*(\{.*?\})\s*\}",
                        re.S)
_SQL_BLOCK = re.compile(r"```(?:sql|duckdb)?\s*\n?(select\b.*?|with\b.*?)```", re.I | re.S)


def tool_calls_from(msg: dict, known: set[str]) -> list[dict]:
    """Native tool calls, or ones a small model wrote as JSON in its reply text."""
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({"name": fn.get("name", ""), "arguments": args or {}})
    if calls:
        return calls
    for name, raw in _JSON_CALL.findall(msg.get("content") or ""):
        if name in known:
            try:
                calls.append({"name": name, "arguments": json.loads(raw)})
            except json.JSONDecodeError:
                continue
    return calls


def sql_from_text(text: str) -> str | None:
    m = _SQL_BLOCK.search(text or "")
    return m.group(1).strip() if m else None
