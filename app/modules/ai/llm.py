"""Optional Claude wording layer (DECISIONS D-21).

The rule-based engine computes every number and ranking. When AI_API_KEY is set, Claude only
rewrites the Arabic explanation text. Any failure, timeout, or refusal returns None, and the
caller keeps the rule-based text, so the demo always works.
"""

import json
import logging
from functools import lru_cache

import anthropic

from app.core.config import get_settings

logger = logging.getLogger("sila.ai.llm")

_SYSTEM = (
    "You write short, clear Modern Standard Arabic explanations for a gold marketplace in Iraq "
    "(منصة صِلة). Keep every number exactly as given. Do not add facts, prices or advice that "
    "are not in the input. No markdown. Each text at most two sentences."
)


@lru_cache
def _client(api_key: str) -> anthropic.Anthropic:
    settings = get_settings()
    return anthropic.Anthropic(api_key=api_key, timeout=settings.ai_timeout_seconds, max_retries=0)


def enabled() -> bool:
    return bool(get_settings().ai_api_key)


def _ask(prompt: str) -> str | None:
    settings = get_settings()
    try:
        response = _client(settings.ai_api_key).beta.messages.create(
            model=settings.ai_model,
            max_tokens=2048,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        logger.warning("AI provider call failed, using rule-based text: %s", exc)
        return None
    if response.stop_reason == "refusal":
        logger.warning("AI provider refused, using rule-based text")
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


def rewrite(text: str) -> str | None:
    """Rewrite one explanation. Returns None to keep the original."""
    if not enabled():
        return None
    return _ask(f"Rewrite this explanation, same meaning and numbers:\n{text}")


def rewrite_many(texts: list[str]) -> list[str] | None:
    """Rewrite several explanations. Returns None unless the result is a same-length list."""
    if not enabled() or not texts:
        return None
    reply = _ask(
        "Rewrite each explanation below, same meaning and numbers. Reply with only a JSON array "
        f"of {len(texts)} strings in the same order.\n" + json.dumps(texts, ensure_ascii=False)
    )
    if reply is None:
        return None
    try:
        start, end = reply.index("["), reply.rindex("]") + 1
        rewritten = json.loads(reply[start:end])
    except ValueError:
        return None
    if (
        not isinstance(rewritten, list)
        or len(rewritten) != len(texts)
        or not all(isinstance(t, str) and t.strip() for t in rewritten)
    ):
        return None
    return [t.strip() for t in rewritten]
