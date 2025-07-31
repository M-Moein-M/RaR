import os
import asyncio
import logging
from functools import lru_cache
from typing import List, Optional, Dict, Any, Iterable
from urllib.parse import urlparse

from langchain_community.utilities import SearxSearchWrapper
from langchain_core.tools import tool
from langsmith import traceable

from langchain_openai import ChatOpenAI  # pip install langchain-openai
from langchain_groq import ChatGroq  # used in init_chat_model
from langchain_ollama import ChatOllama  # for Ollama support

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def get_config_value(value: Any) -> Any:
    """
    Helper to unwrap config values that might be strings, dicts, or enums.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value
    return getattr(value, "value", value)


def get_search_params(search_api: str, search_api_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Currently only supports searxng. Filters the provided config to only include allowed parameters.
    """
    SEARCH_API_PARAMS = {
        "searxng": ["max_results", "categories", "engines", "domains"],
    }

    if not search_api_config:
        return {}

    allowed = SEARCH_API_PARAMS.get(search_api, [])
    return {k: v for k, v in search_api_config.items() if k in allowed}


def _normalize_domain(domain: str) -> str:
    """
    Normalize domains by lowercasing and stripping a leading 'www.'.
    """
    d = domain.lower()
    if d.startswith("www."):
        return d[4:]
    return d


def deduplicate_and_format_sources(
    search_response: Iterable[Dict[str, Any]],
    *,
    max_tokens_per_source: int = 5000,
    include_raw_content: bool = True,
) -> str:
    """
    Takes a list of search response structures and formats deduplicated sources.

    Each element in search_response is expected to be a dict containing:
        - query: str
        - results: List[dict] with keys title, url, content, score, raw_content (optional)

    Returns a single formatted string suitable for LLM context ingestion.
    """
    sources_list = []
    for response in search_response:
        results = response.get("results", [])
        if not isinstance(results, list):
            logger.warning("Expected list of results for response %r, got %r", response.get("query"), type(results))
            continue
        sources_list.extend(results)

    # Deduplicate by URL (preserving first occurrence)
    unique_sources: Dict[str, Dict[str, Any]] = {}
    for source in sources_list:
        url = source.get("url", "")
        norm_url = url.strip()
        if not norm_url:
            continue
        if norm_url in unique_sources:
            continue
        unique_sources[norm_url] = source

    parts: List[str] = []
    parts.append("Content from sources:")
    for source in unique_sources.values():
        title = source.get("title", "<no title>")
        url = source.get("url", "<no url>")
        content = source.get("content", "")
        raw_content = source.get("raw_content", "") or ""

        parts.append("=" * 80)
        parts.append(f"Source: {title}")
        parts.append("-" * 80)
        parts.append(f"URL: {url}")
        parts.append("===")
        parts.append(f"Most relevant content from source: {content}")
        parts.append("===")
        if include_raw_content:
            char_limit = max_tokens_per_source * 4
            if len(raw_content) > char_limit:
                truncated = raw_content[:char_limit] + "... [truncated]"
            else:
                truncated = raw_content
            parts.append(f"Full source content limited to {max_tokens_per_source} tokens: {truncated}")
        parts.append("=" * 80)
        parts.append("")

    return "\n".join(parts).strip()


def _get_searxng_host_from_env() -> str:
    """
    Resolve the SearXNG host from environment variable, defaulting to localhost.
    """
    return os.getenv("SEARXNG_HOST", "http://localhost:8080").rstrip("/")


def _get_default_allowed_domains() -> List[str]:
    """
    Read allowed domains for SearXNG from environment. Comma-separated.
    Defaults to ['radiopaedia.org'] if not set.
    """
    raw = os.getenv("SEARXNG_ALLOWED_DOMAINS", "").strip()
    if raw:
        domains = [d.strip() for d in raw.split(",") if d.strip()]
        if domains:
            return domains
    return ["radiopaedia.org"]


# ---------------------------------------------------------------------
# SearXNG Search Implementation
# ---------------------------------------------------------------------

@traceable
async def searxng_search_async(
    search_queries: List[str],
    *,
    max_results: int = 10,
    searx_host: Optional[str] = None,
    categories: Optional[List[str]] = None,
    engines: Optional[List[str]] = None,
    domains: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Search a SearXNG instance asynchronously. Returns structured results suitable for formatting.

    Domain filtering is applied if `domains` is provided (falls back to env-defined).
    """
    host = searx_host or _get_searxng_host_from_env()
    unsecure = host.startswith("http://")
    wrapper = SearxSearchWrapper(
        searx_host=host,
        unsecure=unsecure,
        categories=",".join(categories) if categories else None,
        engines=engines if engines else None,
    )

    effective_domains = domains if domains is not None else _get_default_allowed_domains()
    allowed_domains_normalized = {_normalize_domain(d) for d in effective_domains}

    async def _one(query: str) -> Dict[str, Any]:
        original_query = query
        if effective_domains:
            site_clause = " OR ".join(f"site:{d}" for d in effective_domains)
            query = f"{site_clause} {query}"
        loop = asyncio.get_event_loop()
        raw_results = await loop.run_in_executor(
            None, lambda: wrapper.results(query, num_results=max_results)
        )

        results = []
        for i, r in enumerate(raw_results):
            url = r.get("link", "") or ""
            parsed = urlparse(url)
            domain = _normalize_domain(parsed.netloc)
            if allowed_domains_normalized and domain not in allowed_domains_normalized:
                logger.debug("Filtering out result from domain '%s' not in allowed %s: %s", domain, allowed_domains_normalized, url)
                continue

            results.append({
                "title": r.get("title", ""),
                "url": url,
                "content": r.get("snippet", ""),
                "score": max(0.0, 1.0 - i * 0.1),
                "raw_content": r.get("snippet", ""),
            })

        logger.info(
            "Query '%s' returned %d raw hits, %d after filtering/formatting",
            original_query,
            len(raw_results),
            len(results),
        )

        return {
            "query": query,
            "follow_up_questions": None,
            "answer": None,
            "results": results,
        }

    out: List[Dict[str, Any]] = []
    for idx, q in enumerate(search_queries):
        if idx:
            await asyncio.sleep(0.25)  # polite throttling
        out.append(await _one(q))
    return out


@tool
async def searxng_search(
    queries: List[str],
    max_results: int = 16,
    searx_host: Optional[str] = None,
    **kwargs,
) -> str:
    """
    Perform a SearXNG search and return a deduplicated markdown bundle of sources.
    """
    categories = kwargs.get("categories")
    engines = kwargs.get("engines", ["google"])
    domains = kwargs.get("domains")  # explicit override

    structured = await searxng_search_async(
        queries,
        max_results=max_results,
        searx_host=searx_host,
        categories=categories,
        engines=engines,
        domains=domains,
    )
    return deduplicate_and_format_sources(structured)


async def select_and_execute_search(
    search_api: str, query_list: List[str], params_to_pass: Dict[str, Any]
) -> str:
    """
    Dispatch to the supported search API and return the formatted results.
    Only 'searxng' is supported.
    """
    if search_api != "searxng":
        raise ValueError(f"Unsupported search API: {search_api}")
    return await searxng_search(
        query_list,
        **params_to_pass,
    )


# ---------------------------------------------------------------------
# Chat Model Bootstrapper
# ---------------------------------------------------------------------

@lru_cache(maxsize=4)
def _create_custom_chat_model(model_name: str, **common_kwargs) -> ChatOpenAI:
    base_url = (
        os.getenv("CUSTOM_API_URL")
        or os.getenv("CUSTOM_BASE_URL")
        or os.getenv("BASE_URL")
    )
    if not base_url:
        raise ValueError(
            "Set CUSTOM_BASE_URL (or BASE_URL) so the client knows where to connect"
        )

    return ChatOpenAI(
        base_url=base_url.rstrip("/"),
        api_key=os.getenv("CUSTOM_API_KEY", "sk-dummy-key"),  # often ignored by gateways
        model=model_name,
        **common_kwargs,
    )


def init_chat_model(model: str, *, temperature: float = 0, streaming: bool = True, **kwargs):
    """
    Return a chat model instance based on the model string.
    Supported prefixes: ollama:, groq:, custom:, fallback OpenAI-style.
    """
    if model.startswith("ollama:"):
        if ChatOllama is None:
            raise RuntimeError("langchain_community not installed – cannot use Ollama")
        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=model.split(":", 1)[1],
            temperature=temperature,
            streaming=streaming,
            **kwargs,
        )

    if model.startswith("groq:"):
        if ChatGroq is None:
            raise RuntimeError("langchain_groq package missing – install to use Groq")
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable must be set")
        return ChatGroq(
            api_key=api_key,
            base_url=os.getenv("GROQ_BASE_URL"),
            model=model.split(":", 1)[1],
            temperature=temperature,
            streaming=streaming,
            **kwargs,
        )

    if model.startswith("custom:"):
        _, model_name = model.split(":", 1)
        return _create_custom_chat_model(
            model_name,
            temperature=temperature,
            streaming=streaming,
            **kwargs,
        )

    model_name = model.split(":", 1)[1] if ":" in model else model
    return ChatOpenAI(
        model=model_name,
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=temperature,
        streaming=streaming,
        **kwargs,
    )
