"""Tests for the MCP session wrapper.

We don't talk to the real server here — FastMCP is patched with a fake
client so the assertions are deterministic.
"""

from __future__ import annotations

from typing import Any

import pytest

from quran_tui import mcp_quran
from quran_tui.mcp_quran import MCPSession, MCPTool


class _FakeTool:
    def __init__(self, name: str, description: str = "", schema: dict | None = None) -> None:
        self.name = name
        self.title = name.replace("_", " ").title()
        self.description = description
        self.inputSchema = schema or {}


class _FakeContentBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResult:
    def __init__(
        self,
        *,
        data: Any = None,
        text: str | None = None,
        is_error: bool = False,
    ) -> None:
        self.data = data
        self.content = [_FakeContentBlock(text)] if text is not None else []
        self.is_error = is_error


class _FakeClient:
    """Mimics fastmcp.Client just enough for MCPSession."""

    def __init__(self, *_args, **_kwargs) -> None:
        self._open = False
        self.tools_returned: list[_FakeTool] = []
        self.tool_responses: dict[str, _FakeResult] = {}
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        self._open = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._open = False

    def is_connected(self) -> bool:
        return self._open

    async def list_tools(self):
        return list(self.tools_returned)

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        return self.tool_responses.get(name, _FakeResult(text=""))


@pytest.fixture
def fake_client(monkeypatch):
    holder: dict[str, _FakeClient] = {}

    def factory(*args, **kwargs):
        client = _FakeClient(*args, **kwargs)
        holder["instance"] = client
        return client

    monkeypatch.setattr(mcp_quran, "Client", factory)
    return holder


@pytest.mark.asyncio
async def test_open_starts_session_with_empty_tools(fake_client) -> None:
    """Opening with an unseeded fake yields an empty tool cache and
    closes cleanly. Tool population is covered by the next test."""
    session = MCPSession("https://example.invalid/")
    await session.open()
    assert session.list_tools() == []
    assert session.is_open
    await session.close()
    assert not session.is_open


@pytest.mark.asyncio
async def test_open_returns_tools_from_fake(fake_client) -> None:
    # Configure the factory to bake in tools.
    real_factory = mcp_quran.Client

    def factory(*args, **kwargs):
        c = real_factory(*args, **kwargs)
        c.tools_returned = [
            _FakeTool("search_tafsir", "search tafsir"),
            _FakeTool("list_editions", "list editions"),
        ]
        return c

    mcp_quran.Client = factory  # type: ignore[assignment]
    try:
        session = MCPSession("https://example.invalid/")
        await session.open()
        names = [t.name for t in session.list_tools()]
        assert names == ["search_tafsir", "list_editions"]
        assert isinstance(session.tool("search_tafsir"), MCPTool)
        assert session.tool("nonexistent") is None
        await session.close()
    finally:
        mcp_quran.Client = real_factory  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_call_unwraps_json_text_when_data_is_none(fake_client) -> None:
    real_factory = mcp_quran.Client

    def factory(*args, **kwargs):
        c = real_factory(*args, **kwargs)
        c.tools_returned = [_FakeTool("search_quran")]
        c.tool_responses = {
            "search_quran": _FakeResult(
                data=None,
                text='{"results": [{"ayah_key": "2:143", "text": "..."}]}',
            ),
        }
        return c

    mcp_quran.Client = factory  # type: ignore[assignment]
    try:
        session = MCPSession("https://example.invalid/")
        await session.open()
        outcome = await session.call("search_quran", {"query": "x"})
        assert outcome.ok
        assert isinstance(outcome.data, dict)
        assert outcome.data["results"][0]["ayah_key"] == "2:143"
        await session.close()
    finally:
        mcp_quran.Client = real_factory  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_call_caches_grounding_nonce_across_canonical_tools(fake_client) -> None:
    real_factory = mcp_quran.Client

    captured: list[dict] = []

    def factory(*args, **kwargs):
        c = real_factory(*args, **kwargs)
        c.tools_returned = [_FakeTool("search_quran")]
        c.tool_responses = {
            "fetch_grounding_rules": _FakeResult(
                data={"grounding_nonce": "abc-123"}
            ),
            "search_quran": _FakeResult(
                data={"results": []},
            ),
        }
        # Wrap call_tool to record arguments.
        orig_call = c.call_tool

        async def recording(name, arguments):
            captured.append({"name": name, "args": dict(arguments)})
            return await orig_call(name, arguments)

        c.call_tool = recording
        return c

    mcp_quran.Client = factory  # type: ignore[assignment]
    try:
        session = MCPSession("https://example.invalid/")
        await session.open()

        # First canonical call → fetches nonce + passes it.
        outcome = await session.call("search_quran", {"query": "first"})
        assert outcome.ok
        nonce_calls = [c for c in captured if c["name"] == "fetch_grounding_rules"]
        assert len(nonce_calls) == 1, "should fetch nonce on first canonical call"
        first_call = next(c for c in captured if c["name"] == "search_quran")
        assert first_call["args"].get("grounding_nonce") == "abc-123"

        # Second call → nonce is cached, no second fetch.
        await session.call("search_quran", {"query": "second"})
        nonce_calls = [c for c in captured if c["name"] == "fetch_grounding_rules"]
        assert len(nonce_calls) == 1
        await session.close()
    finally:
        mcp_quran.Client = real_factory  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_call_surfaces_errors_without_crashing(fake_client) -> None:
    real_factory = mcp_quran.Client

    def factory(*args, **kwargs):
        c = real_factory(*args, **kwargs)
        c.tools_returned = [_FakeTool("search_quran")]

        async def boom(name, arguments):
            raise RuntimeError("network blew up")

        c.call_tool = boom
        return c

    mcp_quran.Client = factory  # type: ignore[assignment]
    try:
        session = MCPSession("https://example.invalid/")
        await session.open()
        outcome = await session.call("search_quran", {"query": "x"})
        assert outcome.ok is False
        assert "network blew up" in (outcome.error or "")
        await session.close()
    finally:
        mcp_quran.Client = real_factory  # type: ignore[assignment]
