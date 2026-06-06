"""V9 embed-endpoint tests. Run from llm_gatewayV9/:  uv run pytest -v tests/test_embed.py

Markers:
  - network: requires GEMINI_API_KEY in ../.env and outbound HTTPS

The tests start an in-process httpx ASGI client against the V9 FastAPI app.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Add parent dir to path so `import main` finds V9's modules.
HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
load_dotenv(HERE.parent / ".env")  # same .env as V3

EXPECTED_FALLBACK_DIM = 768  # gemini-embedding-001 with outputDimensionality=768


def test_gemini_embedder_uses_env_rate_limits(monkeypatch):
    import embedders as E

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_RPM", "4000")
    monkeypatch.delenv("EMBED_GEMINI_RPM", raising=False)
    monkeypatch.delenv("GEMINI_EMBED_RPM", raising=False)
    monkeypatch.delenv("EMBED_GEMINI_COOLDOWN", raising=False)
    monkeypatch.delenv("GEMINI_EMBED_COOLDOWN", raising=False)

    embedders, _ = E.build_embedders()
    gemini = next(e for e in embedders if e.name == "gemini")

    assert gemini.state.rpm == 4000
    assert gemini.state.cooldown == pytest.approx(60 / 4000)


def test_build_embedders_is_gemini_only(monkeypatch):
    import embedders as E

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:31b")
    monkeypatch.setenv("EMBED_ORDER", "ollama,openrouter")

    embedders, order = E.build_embedders()

    assert order == ["gemini"]
    assert [e.name for e in embedders] == ["gemini"]


def test_gemini_embedder_prefers_embed_specific_limits(monkeypatch):
    import embedders as E

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_RPM", "4000")
    monkeypatch.setenv("EMBED_GEMINI_RPM", "3000")
    monkeypatch.setenv("EMBED_GEMINI_COOLDOWN", "0")

    embedders, _ = E.build_embedders()
    gemini = next(e for e in embedders if e.name == "gemini")

    assert gemini.state.rpm == 3000
    assert gemini.state.cooldown == 0


def test_tiny_cooldown_remainder_does_not_block_router_pick():
    import router as R

    state = R.RateState()
    limits = {"rpm": 4000, "rpd": 0, "tpm": 0, "cooldown": 60 / 4000}
    state.record(0)

    ok, why = state.can_use(limits, est_tokens=1)

    assert ok is True
    assert why is None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client():
    """In-process ASGI client. Manually drives FastAPI's lifespan so
    app.state.embedders is wired before any test sends a request."""
    import main as M
    transport = httpx.ASGITransport(app=M.app)
    async with M.app.router.lifespan_context(M.app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=60,
        ) as c:
            yield c


@pytest.mark.asyncio
async def test_ollama_embedder_is_not_registered(client):
    r = await client.post("/v1/embed", json={
        "text": "the quick brown fox",
        "task_type": "retrieval_document",
        "provider": "ollama",
    })
    assert r.status_code == 400, r.text
    assert "unknown embedder 'ollama'" in r.text


@pytest.mark.network
@pytest.mark.asyncio
async def test_fallback_embed(client):
    """Hits Gemini gemini-embedding-001; asserts shape and dim > 0 (stable)."""
    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    r = await client.post("/v1/embed", json={
        "text": "the quick brown fox",
        "task_type": "retrieval_document",
        "provider": "gemini",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    print("gemini:", {k: v for k, v in d.items() if k != "embedding"}, "vec[0:3]:", d["embedding"][:3])
    assert d["provider"] == "gemini"
    assert d["model"]
    assert d["dim"] == EXPECTED_FALLBACK_DIM > 0
    assert isinstance(d["embedding"], list) and len(d["embedding"]) == d["dim"]


@pytest.mark.network
@pytest.mark.asyncio
async def test_default_embed_uses_gemini(client):
    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")

    r = await client.post("/v1/embed", json={
        "text": "use gemini embeddings",
        "task_type": "retrieval_query",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["provider"] == "gemini"
    assert d["attempted"] == []
