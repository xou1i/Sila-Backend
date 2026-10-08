"""Advisor language-model providers behind one call: complete(system, user) -> str | None.

groq and gemini speak the OpenAI-compatible Chat Completions API (stdlib HTTP, like
market/provider.py); anthropic reuses the SDK client of llm.py. The provider is chosen with
ADVISOR_PROVIDER only, no code change. Every failure (timeout, 429, 5xx, refusal, malformed or
truncated reply) returns None and the caller keeps the rule-based answer. The API key is sent
in a header only and never logged.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

import anthropic

from app.core.config import get_settings
from app.modules.ai import llm

logger = logging.getLogger("sila.ai.advisor")

# Used when ADVISOR_BASE_URL / ADVISOR_MODEL are empty. Model names change: check the
# provider's model list before relying on these defaults.
DEFAULT_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}
# Cloudflare in front of some providers (Groq) rejects urllib's default "Python-urllib"
# User-Agent with 403 (error 1010) before the request reaches the API.
USER_AGENT = "sila-backend/1.0"

DEFAULT_MODELS = {
    # llama-3.3-70b-versatile was shut down on 2026-08-16; Groq's replacement
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-3.8-flash",
}


def enabled() -> bool:
    settings = get_settings()
    if settings.advisor_provider == "none":
        return False
    if settings.advisor_provider == "anthropic":
        return bool(settings.advisor_api_key or settings.ai_api_key)
    return bool(settings.advisor_api_key)


def complete(system: str, user: str) -> str | None:
    """The model's text, or None to keep the rule-based answer."""
    if not enabled():
        return None
    provider = get_settings().advisor_provider
    if provider == "anthropic":
        return _anthropic(system, user)
    return _openai_compatible(provider, system, user)


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float) -> Any:
    """One HTTPS POST. Kept separate so tests replace it (no real provider call in tests)."""
    request = urllib.request.Request(  # noqa: S310 (callers pass https URLs only)
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https checked)
        return json.loads(response.read().decode("utf-8"))


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read(300).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return "no body"
    return " ".join(body.split())[:200] or "no body"


def _openai_compatible(provider: str, system: str, user: str) -> str | None:
    settings = get_settings()
    base_url = (settings.advisor_base_url or DEFAULT_BASE_URLS[provider]).rstrip("/")
    if not base_url.startswith("https://"):
        logger.warning("Advisor base URL must be https; using the rule-based answer")
        return None
    payload = {
        "model": settings.advisor_model or DEFAULT_MODELS[provider],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": settings.advisor_max_output_tokens,
        "temperature": 0.3,
    }
    try:
        body = _post_json(
            f"{base_url}/chat/completions",
            payload,
            settings.advisor_api_key,
            settings.advisor_timeout_seconds,
        )
    except urllib.error.HTTPError as exc:
        # 429 (provider rate limit), 401 (bad key), 5xx: same fallback. The provider's error
        # text (never the key, which travels in a header) helps tell these apart in the logs.
        logger.warning(
            "Advisor provider %s answered HTTP %s (%s); using rules",
            provider,
            exc.code,
            _error_text(exc),
        )
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("Advisor provider %s failed (%s); using rules", provider, type(exc).__name__)
        return None
    try:
        choice = body["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Advisor provider %s sent a malformed reply; using rules", provider)
        return None
    # "length" = cut off mid-answer, "content_filter" = refused: neither is a usable answer
    if choice.get("finish_reason") not in (None, "stop"):
        logger.warning("Advisor provider %s stopped early (%s)", provider, choice["finish_reason"])
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None


def _anthropic(system: str, user: str) -> str | None:
    settings = get_settings()
    client = llm._client(settings.advisor_api_key or settings.ai_api_key).with_options(
        timeout=settings.advisor_timeout_seconds
    )
    try:
        response = client.messages.create(
            model=settings.advisor_model or settings.ai_model,
            max_tokens=settings.advisor_max_output_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as exc:
        logger.warning("Advisor provider anthropic failed (%s); using rules", type(exc).__name__)
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        logger.warning("Advisor provider anthropic stopped early (%s)", response.stop_reason)
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None
