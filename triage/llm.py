"""Chat models served by Crusoe Managed Inference (OpenAI-compatible API), plus JSON helpers."""

import json
import os
import re
import sys
import threading
import time
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from triage import config  # noqa: F401  (loads .env)

DEFAULT_BASE_URL = "https://api.inference.crusoecloud.com/v1"
DEFAULT_MODELS = {
    "worker": "openai/gpt-oss-120b",
    "optimizer": "deepseek-ai/DeepSeek-V4-Pro",
}


class Usage:
    """Thread-safe token counter across all Crusoe calls in this process."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = self.input_tokens = self.output_tokens = 0

    def add(self, meta: dict | None):
        with self._lock:
            self.calls += 1
            if meta:
                self.input_tokens += meta.get("input_tokens", 0)
                self.output_tokens += meta.get("output_tokens", 0)

    def snapshot(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


USAGE = Usage()


def base_url() -> str:
    return os.environ.get("CRUSOE_BASE_URL") or DEFAULT_BASE_URL


def api_key() -> str:
    key = os.environ.get("CRUSOE_API_KEY")
    if not key:
        raise RuntimeError("CRUSOE_API_KEY is not set. Copy .env.example to .env and add your Crusoe key.")
    return key


def model_name(kind: Literal["worker", "optimizer"]) -> str:
    return os.environ.get(f"CRUSOE_{kind.upper()}_MODEL") or DEFAULT_MODELS[kind]


def chat_model(kind: Literal["worker", "optimizer"] = "worker", temperature: float = 0.0) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=base_url(),
        api_key=api_key(),
        model=model_name(kind),
        temperature=temperature,
        max_retries=4,
        timeout=120,
    )


def parse_json(text: str):
    """Pull the first JSON object or array out of a model reply (tolerates ```json fences and prose)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model reply")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


def _content(msg) -> str:
    c = msg.content
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return c


def _invoke_with_rate_limit_backoff(llm, msgs, retries: int = 3):
    for attempt in range(retries + 1):
        try:
            return llm.invoke(msgs)
        except Exception as exc:
            rate_limited = getattr(exc, "status_code", None) == 429 or type(exc).__name__.endswith("RateLimitError")
            if not rate_limited or attempt == retries:
                raise
            delay = 15 * (attempt + 1)
            print(f"[rate-limit] waiting {delay}s before retry {attempt + 1}/{retries}", file=sys.stderr)
            time.sleep(delay)


def invoke_json(llm, system: str, user: str, retries: int = 1):
    msgs = [SystemMessage(system), HumanMessage(user)]
    for attempt in range(retries + 1):
        resp = _invoke_with_rate_limit_backoff(llm, msgs)
        USAGE.add(getattr(resp, "usage_metadata", None))
        text = _content(resp)
        try:
            return parse_json(text)
        except (ValueError, json.JSONDecodeError):
            if attempt == retries:
                raise
            msgs += [AIMessage(text), HumanMessage("That was not valid JSON. Reply with only the JSON, nothing else.")]
