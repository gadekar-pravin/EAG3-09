from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))


def test_build_providers_ignores_non_gemini_keys(monkeypatch):
    import providers as P

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "github-key")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:31b")

    providers = P.build_providers(cache_store=None)

    assert list(providers) == ["gemini"]


def test_router_provider_pool_is_disabled(monkeypatch):
    import providers as P

    monkeypatch.setenv("CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "github-key")

    assert P.build_router_providers() == {}


def test_worker_order_and_tier_mapping_are_gemini_only():
    import main as M

    assert M.DEFAULT_ORDER == ["gemini"]
    assert M.TIER_TO_ORDER == {"TINY": ["gemini"], "LARGE": ["gemini"]}


@pytest.mark.asyncio
async def test_auto_route_uses_deterministic_fallback_without_router_pool():
    import main as M
    from router import RouterPool

    decision = await M._classify_tier(
        req=None,
        role="decision",
        router_pool=RouterPool({}, []),
        prompt_text="short factual request",
    )

    assert decision.tier == "TINY"
    assert decision.router_provider == "(unavailable)"
    assert decision.router_model == "(unavailable)"
    assert decision.fallback_used is True


@pytest.mark.asyncio
async def test_provider_endpoint_exposes_only_active_gemini(monkeypatch):
    import main as M

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    monkeypatch.setenv("LLM_ORDER", "nvidia,groq")

    transport = httpx.ASGITransport(app=M.app)
    async with M.app.router.lifespan_context(M.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/providers")

    assert response.status_code == 200
    data = response.json()
    assert data["order"] == ["gemini"]
    assert data["providers"] == ["gemini"]
    assert set(data["limits"]) == {"gemini"}
    assert set(data["models"]) == {"gemini"}
    assert set(data["shortcuts"].values()) == {"gemini"}
