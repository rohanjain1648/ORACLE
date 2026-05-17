"""Web search client supporting Tavily, Brave, and no-op fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: float = 0.0


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class TavilySearch:
    def __init__(self, api_key: str) -> None:
        from tavily import TavilyClient
        self._client = TavilyClient(api_key=api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        resp = self._client.search(query=query, max_results=max_results, search_depth="advanced")
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                score=r.get("score", 0.0),
            )
            for r in resp.get("results", [])
        ]


class NoOpSearch:
    """Used when no search API key is configured."""

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return []


def make_search_provider(provider: str, api_key: str) -> SearchProvider:
    if provider == "tavily" and api_key:
        return TavilySearch(api_key)
    return NoOpSearch()


def format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return "No search results available."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}\n{r.content[:400]}\n")
    return "\n".join(lines)
