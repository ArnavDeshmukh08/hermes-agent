"""Hermes Prime — Stream A LLM client.

stdlib-only LLM wrapper. Deterministic offline mock via HERMES_LLM_MOCK (used
by tests); otherwise tries local Ollama, then Groq, raising LLMError if both
fail. The mock's json_only shape is the CMO contract other streams parse.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class RateLimitError(LLMError):
    """Provider returned 429/503 (or rate-limit text). Eligible for backoff retry."""


def _is_transient(exc) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (429, 500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, RateLimitError, TimeoutError))


def _truthy(v):
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def estimate_tokens(text):
    """Cheap heuristic: ~4 chars/token, floor of 1."""
    return max(1, len(text or "") // 4)


# ---------------------------------------------------------------------------
# Mock (deterministic, offline)
# ---------------------------------------------------------------------------
def _first_words(text, n=10):
    words = re.findall(r"\S+", text or "")
    return " ".join(words[:n])


def _mock_complete(system, user, json_only):
    if json_only:
        grounding = _first_words(user, 10) or "the brief"
        payload = {
            "variants": [
                {
                    "text": f"A contrarian take on {grounding}. The boring follow-up gap is where clinics lose patients.",
                    "angle": "contrarian",
                    "hook": 4,
                    "clarity": 4,
                    "voice_match": 5,
                    "cta": 4,
                },
                {
                    "text": f"A short story drawn from {grounding}. We watched one clinic recover lost patients with a single nudge.",
                    "angle": "story",
                    "hook": 3,
                    "clarity": 4,
                    "voice_match": 4,
                    "cta": 3,
                },
            ]
        }
        return json.dumps(payload)
    head = (user or "").strip().replace("\n", " ")[:120]
    return f"Summary: {head}"


# ---------------------------------------------------------------------------
# Real providers
# ---------------------------------------------------------------------------
# A browser-like UA so Cloudflare-fronted APIs (Groq) don't 403/1010-ban the
# default "Python-urllib/x.y" agent. The openai SDK sets one too; we match it.
_UA = "Mozilla/5.0 (X11; Linux x86_64) hermes-leadgen/1.0"


def _http_post_json(url, payload, headers, timeout):
    data = json.dumps(payload).encode("utf-8")
    headers = {"User-Agent": _UA, **headers}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ollama_complete(system, user, max_tokens, json_only, timeout):
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    url = f"{host}/api/chat"
    payload = {
        "model": os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_predict": int(max_tokens)},
    }
    if json_only:
        payload["format"] = "json"
    body = _http_post_json(url, payload, {"Content-Type": "application/json"}, timeout)
    msg = (body.get("message") or {}).get("content")
    if not msg:
        raise LLMError("ollama returned empty content")
    return msg


def _groq_complete(system, user, max_tokens, json_only, timeout):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY not set")
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": int(max_tokens),
        "temperature": 0.4,
    }
    if json_only:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = _http_post_json(url, payload, headers, timeout)
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"groq malformed response: {e}") from e


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def complete(system, user, *, max_tokens=1500, prefer="ollama", json_only=False, timeout=60, retries=None):
    """Return a completion string.

    HERMES_LLM_MOCK truthy -> deterministic offline output (tests).
    Otherwise: try Ollama then Groq (order honors `prefer`). Each provider is
    retried with exponential backoff on transient errors (429/503/conn) so a
    concurrent burst against Groq's free TPM cap survives rate-limits instead of
    silently producing empty output (LEAD-ENGINE §3). Raises LLMError if all fail.
    """
    if _truthy(os.environ.get("HERMES_LLM_MOCK", "")):
        return _mock_complete(system, user, json_only)

    if json_only:
        system = (system or "") + "\n\nRespond with valid JSON only. No prose, no code fences."

    if retries is None:
        retries = int(os.environ.get("HERMES_LLM_RETRIES", "3"))

    providers = [_ollama_complete, _groq_complete]
    if prefer == "groq":
        providers = [_groq_complete, _ollama_complete]

    errors = []
    for fn in providers:
        for attempt in range(max(1, retries)):
            try:
                return fn(system, user, max_tokens, json_only, timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, LLMError, ValueError) as e:
                errors.append(f"{fn.__name__}[try{attempt + 1}]: {e}")
                if _is_transient(e) and attempt < retries - 1:
                    time.sleep(0.5 * (2**attempt))  # 0.5s, 1s, 2s ...
                    continue
                break  # non-transient or out of retries -> next provider
    raise LLMError("all LLM providers failed: " + " | ".join(errors))


# ---------------------------------------------------------------------------
# Smoke check
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["HERMES_LLM_MOCK"] = "1"
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 1

    out = complete("you are a CMO", "Write about clinic patient retention and no-shows", json_only=True)
    data = json.loads(out)
    assert isinstance(data["variants"], list) and len(data["variants"]) == 2
    for v in data["variants"]:
        for k in ("text", "angle", "hook", "clarity", "voice_match", "cta"):
            assert k in v
        assert 0 <= v["hook"] <= 5
    # grounding: echoes words from the user prompt
    assert "clinic" in data["variants"][0]["text"].lower()

    summary = complete("sys", "Patient retention is a systems problem not a charisma problem", json_only=False)
    assert summary.startswith("Summary:")
    print("llm OK")
