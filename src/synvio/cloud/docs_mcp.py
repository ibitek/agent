"""Public OpenAI documentation, shared by the two execution paths."""

import json
import re
from urllib.parse import urlsplit

import httpx
from openjarvis.mcp.protocol import MCPError

from .common import redact_data

URL = "https://developers.openai.com/mcp"
SEARCH = "search_openai_docs"
FETCH = "fetch_openai_doc"


def requested_topic(message):
    match = re.fullmatch(
        r"(?:search (?:the )?openai docs for|look up in (?:the )?openai docs|"
        r"sprawd[zź] w dokumentacji openai|wyszukaj w dokumentacji openai)"
        r"[ :]+([\w .,+()/?!-]{2,160})",
        message.strip(), re.IGNORECASE,
    )
    if match:
        topic = match[1]
        if redact_data(topic) == topic and not re.search(
            r"\b(?:sk-[\w-]+|(?:token|password|hasło|sekret|secret)\s+[\w-]{20,})\b",
            topic, re.IGNORECASE,
        ):
            return topic
    return None


def lookup(topic):
    from openjarvis.mcp.client import MCPClient
    from openjarvis.mcp.transport import StreamableHTTPTransport

    client = MCPClient(StreamableHTTPTransport(URL, connect_timeout=3, request_timeout=5))
    try:
        client.initialize()
        result = client.call_tool(SEARCH, {"query": topic, "limit": 3})
        if result.get("isError"):
            return []
        payload = json.loads(result["content"][0]["text"])
        sources = []
        for hit in payload.get("hits", [])[:3]:
            url = hit.get("url", "")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.netloc != "developers.openai.com":
                continue
            sections = hit.get("hierarchy") or {}
            sources.append({
                "url": url[:500],
                "title": " / ".join(str(title) for title in sections.values() if title)[:220],
                "excerpt": (hit.get("content") or "")[:1400],
            })
        return sources
    except (MCPError, httpx.HTTPError, RuntimeError, ValueError, KeyError, IndexError):
        return []
    finally:
        client.close()
