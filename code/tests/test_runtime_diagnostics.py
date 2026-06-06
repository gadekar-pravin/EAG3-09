from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flow
import mcp_runner
import memory
import skills
from schemas import MemoryItem

GATEWAY_CLIENT = Path(__file__).resolve().parents[2] / "llm_gatewayV9" / "client.py"


class _Store:
    def write_node(self, *_args, **_kwargs) -> None:
        return None


class _Registry:
    def get(self, name: str):
        return type("Skill", (), {"name": name})()


@pytest.mark.asyncio
async def test_run_one_expands_exception_group_and_keeps_prompt(monkeypatch) -> None:
    async def boom(*_args, **_kwargs):
        exc = ExceptionGroup(
            "tool nursery failed",
            [RuntimeError("mcp subprocess died"), ValueError("bad tool payload")],
        )
        setattr(exc, "prompt_sent", "RENDERED PROMPT")
        raise exc

    graph = type("Graph", (), {"g": nx.DiGraph()})()
    graph.g.add_node("n:1", skill="retriever", inputs=["USER_QUERY"], metadata={})
    executor = flow.Executor.__new__(flow.Executor)
    executor.registry = _Registry()
    monkeypatch.setattr(flow, "run_skill", boom)

    nid, result, prompt = await executor._run_one(
        "n:1", graph, "sid", "question", _Store(), []
    )

    assert nid == "n:1"
    assert result.success is False
    assert prompt == "RENDERED PROMPT"
    assert "ExceptionGroup: tool nursery failed" in result.error
    assert "RuntimeError: mcp subprocess died" in result.error
    assert "ValueError: bad tool payload" in result.error
    assert "traceback:" in result.error


class _FailingSession:
    def __init__(self, *_args):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def initialize(self):
        raise RuntimeError("initialize exploded")


class _Stdio:
    async def __aenter__(self):
        return object(), object()

    async def __aexit__(self, *_args):
        return False


class _SuccessfulSession:
    def __init__(self, *_args):
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        text = '{"found": false, "chunks": [], "summary": "no local hit"}'
        return type("ToolResult", (), {"content": [type("Text", (), {"text": text})()]})()


@pytest.mark.asyncio
async def test_run_with_tools_reports_mcp_lifecycle_phase(monkeypatch) -> None:
    monkeypatch.setattr(mcp_runner, "stdio_client", lambda _params: _Stdio())
    monkeypatch.setattr(mcp_runner, "ClientSession", _FailingSession)

    with pytest.raises(mcp_runner.ToolLoopError) as ei:
        await mcp_runner.run_with_tools(
            prompt="prompt",
            tools_payload=[],
            agent="retriever",
            session_id="sid",
        )

    msg = str(ei.value)
    assert "mcp tool loop failed during mcp_initialize" in msg
    assert "RuntimeError: initialize exploded" in msg


@pytest.mark.asyncio
async def test_run_with_tools_pins_continuation_to_tool_call_provider(monkeypatch) -> None:
    monkeypatch.setattr(mcp_runner, "stdio_client", lambda _params: _Stdio())
    monkeypatch.setattr(mcp_runner, "ClientSession", _SuccessfulSession)
    provider_pins: list[str | None] = []

    async def fake_chat(**kwargs):
        provider_pins.append(kwargs["provider_pin"])
        if len(provider_pins) == 1:
            return {
                "provider": "github",
                "text": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "search_knowledge",
                        "arguments": {"query": "Claude Shannon", "k": 5},
                    }
                ],
            }
        return {"provider": "github", "text": '{"found": false}', "tool_calls": []}

    monkeypatch.setattr(mcp_runner, "_chat", fake_chat)

    reply = await mcp_runner.run_with_tools(
        prompt="prompt",
        tools_payload=[{"name": "search_knowledge"}],
        agent="retriever",
        session_id="sid",
    )

    assert reply["text"] == '{"found": false}'
    assert provider_pins == [None, "github"]


def test_gateway_client_error_includes_response_body(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("gateway_client_under_test", GATEWAY_CLIENT)
    assert spec and spec.loader
    gateway_client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway_client)

    def fake_post(url, **_kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(
            502,
            request=request,
            text='{"detail":"gemini failed: missing thought_signature"}',
        )

    monkeypatch.setattr(gateway_client.httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError) as ei:
        gateway_client.LLM().chat(messages=[{"role": "user", "content": "hi"}])

    assert "502 Bad Gateway" in str(ei.value)
    assert "response=" in str(ei.value)
    assert "missing thought_signature" in str(ei.value)


def test_memory_embed_failure_logs_gateway_body_and_keyword_fallback(
    monkeypatch, capsys
) -> None:
    request = httpx.Request("POST", "http://localhost:8109/v1/embed")
    response = httpx.Response(
        503,
        request=request,
        text='{"detail":"all embedders unavailable. attempts=[{\\"provider\\":\\"ollama\\"}]"}',
    )
    err = httpx.HTTPStatusError("503 Service Unavailable", request=request, response=response)

    def fail_embed(*_args, **_kwargs):
        raise err

    item = MemoryItem(
        id="mem:test",
        kind="fact",
        keywords=["claude", "shannon"],
        descriptor="Claude Shannon biography",
        value={"raw": "Claude Shannon was born in 1916."},
        source="test",
        run_id="run",
    )
    monkeypatch.setattr(memory, "_gateway_embed", fail_embed)
    monkeypatch.setattr(memory, "_load", lambda: [item])

    hits = memory.read("Claude Shannon", top_k=3)

    assert hits == [item]
    out = capsys.readouterr().out
    assert "embedding failed" in out
    assert "response=" in out
    assert "attempts=" in out
    assert "item written without vector" in out


def test_query_echo_memory_hits_are_filtered_from_memory_read(monkeypatch) -> None:
    query = "When was Claude Shannon born and when did he die?"
    echo = MemoryItem(
        id="mem:echo",
        kind="fact",
        descriptor="Birth and death dates of Claude Shannon",
        value={"raw": query},
        source="user_query",
        run_id="old",
    )
    useful = MemoryItem(
        id="mem:fact",
        kind="fact",
        descriptor="Claude Shannon dates",
        value={"raw": "Claude Shannon was born April 30, 1916 and died February 24, 2001."},
        source="researcher",
        run_id="old",
    )

    monkeypatch.setattr(memory, "_try_embed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(memory, "_load", lambda: [echo, useful])

    assert memory.read(query, top_k=5) == [useful]


@pytest.mark.asyncio
async def test_retriever_found_false_is_recoverable_failure(monkeypatch) -> None:
    async def fake_run_with_tools(**_kwargs):
        return {
            "provider": "github",
            "text": '{"found": false, "chunks": [], "summary": "no local hit"}',
        }

    monkeypatch.setattr(mcp_runner, "run_with_tools", fake_run_with_tools)
    skill = SimpleNamespace(
        name="retriever",
        tools_allowed=["search_knowledge"],
        provider_pin=None,
        max_tokens=1200,
        temperature=0.2,
        prompt_template=lambda: "Retriever prompt",
    )
    graph = nx.DiGraph()
    graph.add_node("n:1", inputs=["USER_QUERY"], metadata={})

    result, prompt = await skills.run_skill(
        skill,
        "n:1",
        graph.nodes,
        "sid",
        "When was Claude Shannon born and when did he die?",
        None,
    )

    assert prompt.startswith("Retriever prompt")
    assert result.success is False
    assert result.agent_name == "retriever"
    assert result.output["found"] is False
    assert result.error == "retriever found no relevant knowledge"
