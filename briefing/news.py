"""Curated news fetcher for the morning briefing.

Pulls ~10 raw items from DuckDuckGo across topics relevant to Arnav, then
passes them to the LLM to pick 2-3 genuinely useful ones. Returns [] on any
failure so the briefing is never blocked by a news outage.

Both the search function and the LLM complete function are injectable via the
constructor so tests can mock them without network.
"""

from __future__ import annotations

import json
from typing import Any, Callable

# Topics relevant to Arnav (AI/ML student, Vytal SaaS, StockSage, Jack project).
_QUERIES = [
    "AI agent tools OR Claude Anthropic OR LLM productivity",
    "startup India SaaS",
    "trading algorithms India",
]
_MAX_RAW = 10  # items collected per query before deduplication

_FILTER_SYSTEM = (
    "You are a news curator for Arnav, a 20-year-old AI/ML student in India. "
    "He is building: Vytal (psychiatry-clinic SaaS, India), Jack (personal AI "
    "assistant), and StockSage (automated trading). His interests: AI tools, "
    "agentic workflows, Claude/Anthropic updates, India startup ecosystem, "
    "trading tech. "
    "From the news items I give you, pick 2-3 that are GENUINELY useful to him "
    "— not generic tech news. Exclude anything vague, promotional, or irrelevant. "
    'Return ONLY a JSON array: [{"headline": "...", "summary_one_line": "...", '
    '"why_relevant": "..."}]. If nothing is genuinely relevant, return [].'
)


def _default_search(query: str, max_results: int = _MAX_RAW) -> list[dict]:
    """DuckDuckGo news search with fallback to text search."""
    try:
        from ddgs import DDGS  # noqa: PLC0415
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # noqa: PLC0415
        except ImportError:
            return []
    try:
        ddgs = DDGS()
        if hasattr(ddgs, "news"):
            return list(ddgs.news(query, max_results=max_results))  # type: ignore[union-attr]
        return list(ddgs.text(query, max_results=max_results))
    except Exception:  # noqa: BLE001 — search hiccup must not block the briefing
        return []


def _default_complete(system: str, user: str, **kwargs: Any) -> str:
    """LLM complete with groq + json_only."""
    from lib.llm import complete  # type: ignore[import] — lazy, VPS-only dep

    return complete(system, user, prefer="groq", max_tokens=1500, json_only=True, timeout=60)


class NewsFetcher:
    """Fetch and curate news items relevant to Arnav."""

    def __init__(
        self,
        search_fn: Callable[..., list[dict]] | None = None,
        complete_fn: Callable[..., str] | None = None,
    ) -> None:
        self._search_fn = search_fn or _default_search
        self._complete_fn = complete_fn or _default_complete

    def get_curated_news(self) -> list[dict]:
        """Return 0-3 curated news dicts or [] on any failure.

        Each dict has keys: headline, summary_one_line, why_relevant.
        """
        try:
            raw = self._collect_raw()
            if not raw:
                return []
            return self._filter_with_llm(raw)
        except Exception:  # noqa: BLE001 — outage must never block the briefing
            return []

    def _collect_raw(self) -> list[dict]:
        """Collect up to _MAX_RAW deduplicated items across all queries."""
        seen_titles: set[str] = set()
        items: list[dict] = []
        for query in _QUERIES:
            for r in self._search_fn(query, _MAX_RAW):
                title = (r.get("title") or r.get("headline") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                body = r.get("body") or r.get("excerpt") or r.get("snippet") or ""
                url = r.get("url") or r.get("href") or ""
                items.append({"title": title, "body": body, "url": url})
        return items

    def _filter_with_llm(self, raw: list[dict]) -> list[dict]:
        """Pass raw items to the LLM and parse the returned JSON list."""
        lines = []
        for i, item in enumerate(raw, 1):
            lines.append(f"{i}. {item['title']}")
            if item["body"]:
                lines.append(f"   {item['body'][:200]}")
        user_prompt = "News items:\n" + "\n".join(lines)
        response = self._complete_fn(_FILTER_SYSTEM, user_prompt)
        return _parse_json_list(response)

    def summary_text(self) -> str:
        """Return curated news as short lines or '' if nothing to report."""
        items = self.get_curated_news()
        if not items:
            return ""
        parts = []
        for item in items:
            headline = item.get("headline", "")
            why = item.get("why_relevant", "")
            summary = item.get("summary_one_line", "")
            line = headline
            if summary:
                line += f" — {summary}"
            if why:
                line += f" ({why})"
            parts.append(line)
        return "\n".join(parts)


def _parse_json_list(text: str) -> list[dict]:
    """Extract a JSON list from the LLM response; return [] on any parse error."""
    try:
        text = text.strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                ln for ln in lines if not ln.strip().startswith("```")
            ).strip()
        data = json.loads(text)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        return []
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
