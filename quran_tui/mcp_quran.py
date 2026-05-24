"""Thin async wrapper around the FastMCP client for the quran.ai server.

Holds one persistent client connection for the lifetime of the app, caches the
grounding-rules nonce so canonical-data tool calls don't pay the rules-
injection tax on every request, and surfaces a small typed API for the TUI to
consume (instead of the raw MCP tool envelope).

We deliberately wrap rather than expose ``fastmcp.Client`` directly so the TUI
never has to know about MCP transport details.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client

DEFAULT_SERVER_URL = "https://mcp.quran.ai/"

# Tools that take a ``grounding_nonce`` argument. If we don't pass one, the
# server bundles its grounding-rules payload into every response (wasteful).
# We fetch the nonce once per session and reuse it.
_GROUNDING_AWARE_TOOLS = frozenset(
    {
        "fetch_quran",
        "fetch_translation",
        "fetch_tafsir",
        "search_quran",
        "search_translation",
        "search_tafsir",
    }
)


@dataclass(frozen=True)
class MCPTool:
    """A trimmed-down view of one MCP tool."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


class MCPSession:
    """Persistent FastMCP client + cached server metadata."""

    def __init__(self, server_url: str = DEFAULT_SERVER_URL) -> None:
        self.server_url = server_url
        self._client: Client | None = None
        self._enter_task: asyncio.Task[None] | None = None
        self._open_event = asyncio.Event()
        self._tools_cache: list[MCPTool] = []
        self._grounding_nonce: str | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Establish the MCP session and populate caches."""
        async with self._lock:
            if self._client is not None:
                return
            self._client = Client(self.server_url)
            await self._client.__aenter__()
            raw_tools = await self._client.list_tools()
            self._tools_cache = [
                MCPTool(
                    name=t.name,
                    title=getattr(t, "title", "") or t.name,
                    description=t.description or "",
                    input_schema=dict(t.inputSchema or {}),
                )
                for t in raw_tools
            ]

    async def close(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and self._client.is_connected()

    def list_tools(self) -> list[MCPTool]:
        return list(self._tools_cache)

    def tool(self, name: str) -> MCPTool | None:
        for t in self._tools_cache:
            if t.name == name:
                return t
        return None

    # --- grounding-rules handshake --------------------------------------

    async def _ensure_grounding_nonce(self) -> str | None:
        if self._grounding_nonce is not None:
            return self._grounding_nonce
        if self._client is None:
            return None
        try:
            result = await self._client.call_tool("fetch_grounding_rules", {})
        except Exception:
            return None
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            nonce = data.get("grounding_nonce")
            if isinstance(nonce, str) and nonce:
                self._grounding_nonce = nonce
                return nonce
        return None

    # --- tool dispatch --------------------------------------------------

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Call a tool by name. Adds the grounding nonce automatically when
        the tool's schema accepts one, and parses text-shaped JSON output
        into ``data`` for tools without a declared outputSchema."""
        import json as _json

        if self._client is None:
            raise RuntimeError("MCP session not open")
        args = dict(arguments)
        if name in _GROUNDING_AWARE_TOOLS and "grounding_nonce" not in args:
            nonce = await self._ensure_grounding_nonce()
            if nonce:
                args["grounding_nonce"] = nonce
        try:
            result = await self._client.call_tool(name, args)
        except Exception as exc:
            return ToolOutcome(ok=False, error=str(exc))
        data: Any = getattr(result, "data", None)
        text_blocks: list[str] = []
        content = getattr(result, "content", None) or []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                text_blocks.append(text)
        combined_text = "\n".join(text_blocks)
        # FastMCP often hands back a dataclass / Pydantic model rather than a
        # plain dict. Normalise so downstream renderers can use ordinary dict
        # access.
        import dataclasses

        if data is not None and not isinstance(data, (dict, list, str, int, float, bool)):
            if dataclasses.is_dataclass(data) and not isinstance(data, type):
                try:
                    data = dataclasses.asdict(data)
                except Exception:
                    pass
            else:
                dump = getattr(data, "model_dump", None)
                if callable(dump):
                    try:
                        data = dump(mode="python")
                    except Exception:
                        pass
        # Some tools return structured JSON only in a text content block (no
        # outputSchema). Decode that on the user's behalf so the UI doesn't
        # have to think about it.
        if data is None and combined_text.lstrip().startswith(("{", "[")):
            try:
                data = _json.loads(combined_text)
            except _json.JSONDecodeError:
                pass
        is_error = bool(getattr(result, "is_error", False))
        return ToolOutcome(
            ok=not is_error,
            data=data,
            text=combined_text,
            error=None if not is_error else (text_blocks[0] if text_blocks else "tool error"),
        )


@dataclass
class ToolOutcome:
    """Result of one tool call, with both structured + text representations."""

    ok: bool
    data: Any = None
    text: str = ""
    error: str | None = None
