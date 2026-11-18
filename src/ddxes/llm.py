"""Optional access to a large language model, used only by the free-text intake (ddxes.intake).

Providers are tried in order: Groq, Google Gemini, OpenRouter. All three speak the OpenAI chat-completions protocol,
so one small client on the Python standard library serves them all. Keys come from the environment or from a .env
file at the repository root (template: .env.example; .env itself is never committed). Without a key nothing is sent
anywhere and the intake is simply unavailable; the expert system does not depend on this module.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .paths import ROOT

ENV_FILE = ROOT / ".env"
USER_AGENT = "ddxes/1.0 (educational expert system)"


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    key_var: str
    model_vars: tuple          # environment variables naming models, tried first
    default_models: tuple      # used when no variable names a model


PROVIDERS = (
    Provider("groq", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY",
             ("GROQ_MODEL", "GROQ_FALLBACK_MODEL"), ("qwen/qwen3.8-27b", "openai/gpt-oss-120b")),
    Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY",
             ("GEMINI_MODEL",), ("gemini-flash-latest",)),
    Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY",
             ("OPENROUTER_MODEL", "OPENROUTER_FALLBACK_MODEL"), ()),
)


class LLMError(RuntimeError):
    """No provider gave a usable answer. `attempts` lists (provider, model, reason); it never contains a key."""

    def __init__(self, message: str, attempts: list):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class Reply:
    data: dict
    provider: str
    model: str
    seconds: float
    usage: dict = field(default_factory=dict)


def read_env_file(path=ENV_FILE) -> dict:
    """KEY=VALUE lines of a .env file (comments and blank lines ignored; surrounding quotes removed)."""
    out = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def settings(path=ENV_FILE) -> dict:
    """The .env file overlaid by the process environment (the environment wins)."""
    env = read_env_file(path)
    env.update({k: v for k, v in os.environ.items() if v})
    return env


def configured(providers=None, env: dict | None = None, models=None) -> list:
    """(provider, model, key) for every provider with a key, in priority order (optionally only these models)."""
    env = settings() if env is None else env
    out = []
    for p in PROVIDERS:
        if providers and p.name not in providers:
            continue
        key = env.get(p.key_var, "").strip()
        if not key:
            continue
        names = [env.get(v, "").strip() for v in p.model_vars] + list(p.default_models)
        for m in dict.fromkeys(m for m in names if m):
            if not models or m in models:
                out.append((p, m, key))
    return out


def available(env: dict | None = None) -> bool:
    return bool(configured(env=env))


def describe(env: dict | None = None) -> str:
    """'groq (openai/gpt-oss-120b), then gemini (...)': which models would be tried, without keys."""
    pairs = configured(env=env)
    return ", then ".join(f"{p.name} ({m})" for p, m, _ in pairs) or "none"


def parse_json(text: str) -> dict:
    """The JSON object in a model's answer, tolerating code fences and <think> blocks around it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("no JSON object in the answer") from None
        data = json.loads(text[i:j + 1])
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return data


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies()))


def _post(url: str, key: str, body: dict, timeout: float):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": USER_AGENT})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _error_text(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
        if isinstance(body, list) and body:
            body = body[0]
        msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
    except (ValueError, AttributeError):
        msg = None
    return f"HTTP {e.code}" + (f": {msg[:160]}" if msg else "")


def _brief(provider: str, model: str) -> dict:
    """Keep reasoning models short: the intake needs a JSON answer, not a long chain of thought (without these,
    Gemini thinks for ~30 s and the OpenRouter reasoning models spend the whole token budget before answering)."""
    if provider == "gemini" or (provider == "groq" and model.startswith("openai/gpt-oss")):
        return {"reasoning_effort": "low"}
    if provider == "openrouter":
        return {"reasoning": {"enabled": False}}
    return {}


def chat_json(messages: list, *, providers=None, models=None, max_tokens: int = 1000, temperature: float = 0.0,
              timeout: float = 60.0, rate_wait: float = 20.0, env: dict | None = None) -> Reply:
    """Send a chat to the first provider that answers with a JSON object.

    On a rate limit the same model is retried once if the provider asks to wait at most `rate_wait` seconds, and once
    after a short pause when it is overloaded (HTTP 503); a request the provider rejects (HTTP 400) is retried once
    without the optional settings. Any other failure moves on to the next model, then to the next provider."""
    pairs = configured(providers, env, models)
    if not pairs:
        raise LLMError("no language-model key configured (set GROQ_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY "
                       "in .env)", [])
    attempts = []
    for p, model, key in pairs:
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}, **_brief(p.name, model)}
        for attempt in range(2):
            t0 = time.perf_counter()
            try:
                out = _post(p.url, key, body, timeout)
                content = out["choices"][0]["message"]["content"]
                data = parse_json(content)
                return Reply(data, p.name, model, time.perf_counter() - t0, out.get("usage") or {})
            except urllib.error.HTTPError as e:
                reason = _error_text(e)
                try:
                    wait = float(e.headers.get("retry-after", "")) if e.headers else None
                except ValueError:
                    wait = None
                if e.code == 429 and attempt == 0 and wait is not None and wait <= rate_wait:
                    time.sleep(wait + 0.5)
                    continue
                if e.code == 503 and attempt == 0:
                    time.sleep(2)
                    continue
                if e.code == 400 and attempt == 0:
                    for k in ("response_format", "reasoning_effort", "reasoning"):
                        body.pop(k, None)
                    continue
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = f"no connection: {getattr(e, 'reason', e)}"
            except (KeyError, IndexError, TypeError, ValueError) as e:
                reason = f"unusable answer: {e}"
            attempts.append((p.name, model, reason))
            break
    summary = "; ".join(f"{n} {m}: {r}" for n, m, r in attempts)
    raise LLMError(f"no language model answered ({summary})", attempts)
