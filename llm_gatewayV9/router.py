"""Capability-aware router with configurable provider rate budgets.

Same RPM/RPD bookkeeping as before, but per-provider rate limits are now read
from env (e.g. GEMINI_RPM / GEMINI_RPD / GEMINI_TPM) so a paid-tier key can
lift the conservative free-tier defaults. A zero cap means "do not enforce that
local limit" — upstream 429s still trigger backoff. The per-call cooldown is
derived as 60/rpm, so a higher RPM both raises the cap and widens throughput."""
from __future__ import annotations
import os, time, asyncio
from collections import deque, defaultdict
from copy import deepcopy

import httpx


MIN_ENFORCED_COOLDOWN_WAIT = 0.05


DEFAULT_LIMITS = {
    "ollama":     {"rpm": 0,    "rpd": 0,       "tpm": 0,        "max_ctx": 32000},
    "cerebras":   {"rpm": 30,   "rpd": 9999,    "tpm": 60000,    "max_ctx": 8000,    "tokens_per_day": 1_000_000},
    "groq":       {"rpm": 30,   "rpd": 1000,    "tpm": 6000,     "max_ctx": 100000},
    "nvidia":     {"rpm": 40,   "rpd": 9999,    "tpm": 100000,   "max_ctx": 100000},
    "gemini":     {"rpm": 15,   "rpd": 1000,    "tpm": 250000,   "max_ctx": 1000000},
    "openrouter": {"rpm": 20,   "rpd": 50,      "tpm": 99999999, "max_ctx": 100000},
    "github":     {"rpm": 10,   "rpd": 50,      "tpm": 99999999, "max_ctx": 8000},
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.replace("_", ""))


def _apply_numeric_overrides(limits: dict[str, dict], provider: str) -> None:
    prefix = provider.upper()
    for field in ("rpm", "rpd", "tpm", "max_ctx", "tokens_per_day"):
        env_name = f"{prefix}_{field.upper()}"
        if env_name in os.environ:
            limits[provider][field] = _env_int(env_name, limits[provider].get(field, 0))


def _env_flag(name: str) -> bool | None:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "paid", "payg", "pay-as-you-go"}:
        return True
    if raw in {"0", "false", "no", "free"}:
        return False
    return None


def _add_cooldowns(limits: dict[str, dict]) -> dict[str, dict]:
    for provider_limits in limits.values():
        rpm = provider_limits.get("rpm", 0)
        provider_limits["cooldown"] = 0 if rpm <= 0 else 60 / rpm
    return limits


def load_limits(*, openrouter_paid: bool | None = None) -> dict[str, dict]:
    """Return provider rate budgets with env overrides applied.

    Each provider's caps can be lifted from the free-tier defaults via
    {PROVIDER}_RPM / _RPD / _TPM / _MAX_CTX / _TOKENS_PER_DAY env vars
    (e.g. GEMINI_RPM=4000 for a paid Tier-1 Gemini key).

    OpenRouter is special: a paid account on a non-":free" model has no useful
    local platform RPM/RPD ceiling, so those caps are dropped to 0 (upstream
    429/backoff polices real capacity) unless OPENROUTER_RPM / OPENROUTER_RPD
    are set explicitly. `openrouter_paid` (or OPENROUTER_TIER) selects the tier;
    a ":free" model always keeps free-model caps."""
    limits = deepcopy(DEFAULT_LIMITS)
    for provider in limits:
        _apply_numeric_overrides(limits, provider)

    tier_flag = _env_flag("OPENROUTER_TIER")
    if tier_flag is not None:
        openrouter_paid = tier_flag

    openrouter_model = (os.environ.get("OPENROUTER_MODEL") or "").strip().lower()
    openrouter_free_model = openrouter_model.endswith(":free")
    if openrouter_paid and not openrouter_free_model:
        # No explicit operator cap → let upstream 429s police paid capacity.
        if (os.environ.get("OPENROUTER_RPM") or "").strip() == "":
            limits["openrouter"]["rpm"] = 0
        if (os.environ.get("OPENROUTER_RPD") or "").strip() == "":
            limits["openrouter"]["rpd"] = 0
        limits["openrouter"]["limit_source"] = "paid"
    else:
        limits["openrouter"]["limit_source"] = "free"
    if openrouter_free_model:
        limits["openrouter"]["limit_source"] = "free-model"

    return _add_cooldowns(limits)


LIMITS = load_limits()


def refresh_limits(*, openrouter_paid: bool | None = None) -> None:
    """Recompute the global LIMITS in place (preserving the dict identity that
    importers hold a reference to)."""
    LIMITS.clear()
    LIMITS.update(load_limits(openrouter_paid=openrouter_paid))


async def refresh_openrouter_limits_from_key(provider) -> None:
    """Optionally introspect the OpenRouter account tier at startup.

    OPENROUTER_TIER wins if set. Otherwise, if the provider exposes an api_key,
    query /api/v1/key and treat a non-free-tier account as paid. Any failure
    falls back to the env-configured defaults. Gemini has no comparable
    key-level tier endpoint, so its limits stay operator-configured."""
    tier_flag = _env_flag("OPENROUTER_TIER")
    if tier_flag is not None:
        refresh_limits(openrouter_paid=tier_flag)
        return
    if provider is None or not getattr(provider, "api_key", ""):
        refresh_limits()
        return
    try:
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
        response.raise_for_status()
        data = response.json().get("data") or {}
        refresh_limits(openrouter_paid=not bool(data.get("is_free_tier", True)))
    except Exception:
        refresh_limits()

SHORTCUTS = {
    "g": "gemini", "gem": "gemini", "gemini": "gemini",
    "n": "nvidia", "nv": "nvidia", "nvidia": "nvidia",
    "o": "ollama", "oll": "ollama", "ollama": "ollama",
    "gr": "groq", "groq": "groq",
    "c": "cerebras", "cer": "cerebras", "cerebras": "cerebras",
    "or": "openrouter", "opr": "openrouter", "openrouter": "openrouter",
    "gh": "github", "ghb": "github", "github": "github",
}


def resolve(name):
    if not name:
        return None
    return SHORTCUTS.get(name.lower())


class RateState:
    def __init__(self):
        self.calls_minute = deque()
        self.tokens_minute = deque()
        self.calls_today = 0
        self.tokens_today = 0
        self.day_start = self._day_start()
        self.last_call = 0.0
        self.unavailable_until = 0.0
        self.unavailable_reason = ""

    @staticmethod
    def _day_start():
        now = time.time()
        return now - (now % 86400)

    def gc(self):
        now = time.time()
        if now - self.day_start >= 86400:
            self.calls_today = 0
            self.tokens_today = 0
            self.day_start = self._day_start()
        cutoff = now - 60
        while self.calls_minute and self.calls_minute[0] < cutoff:
            self.calls_minute.popleft()
        while self.tokens_minute and self.tokens_minute[0][0] < cutoff:
            self.tokens_minute.popleft()

    def can_use(self, limits, est_tokens=0):
        self.gc()
        now = time.time()
        if now < self.unavailable_until:
            return False, f"backoff: {self.unavailable_reason} ({self.unavailable_until - now:.0f}s left)"
        cooldown = limits.get("cooldown", 0)
        wait = cooldown - (now - self.last_call)
        if wait > MIN_ENFORCED_COOLDOWN_WAIT:
            return False, f"cooldown ({wait:.1f}s)"
        rpm = limits.get("rpm", 0)
        if rpm > 0 and len(self.calls_minute) >= rpm:
            return False, "RPM limit"
        rpd = limits.get("rpd", 0)
        if rpd > 0 and self.calls_today >= rpd:
            return False, "RPD limit"
        tpm = sum(t for _, t in self.tokens_minute)
        tpm_limit = limits.get("tpm", 0)
        if tpm_limit > 0 and tpm + est_tokens > tpm_limit:
            return False, "TPM limit"
        tokens_per_day = limits.get("tokens_per_day", 0)
        if tokens_per_day > 0 and self.tokens_today + est_tokens > tokens_per_day:
            return False, "daily token cap"
        return True, None

    def record(self, tokens):
        now = time.time()
        self.calls_minute.append(now)
        self.tokens_minute.append((now, tokens))
        self.calls_today += 1
        self.tokens_today += tokens
        self.last_call = now

    def mark_unavailable(self, seconds: float, reason: str):
        self.unavailable_until = time.time() + seconds
        self.unavailable_reason = reason

    def snapshot(self, limits):
        self.gc()
        now = time.time()
        tpm = sum(t for _, t in self.tokens_minute)
        return {
            "rpm_used": len(self.calls_minute),
            "rpm_limit": limits["rpm"],
            "rpd_used": self.calls_today,
            "rpd_limit": limits["rpd"],
            "tpm_used": tpm,
            "tpm_limit": limits["tpm"],
            "tokens_today": self.tokens_today,
            "tokens_per_day": limits.get("tokens_per_day"),
            "cooldown_s": limits.get("cooldown", 0),
            "cooldown_remaining": max(0, limits.get("cooldown", 0) - (now - self.last_call)) if self.last_call else 0,
            "last_call": self.last_call,
            "backoff_remaining": max(0, self.unavailable_until - now),
            "backoff_reason": self.unavailable_reason if now < self.unavailable_until else "",
        }


class Router:
    def __init__(self, providers: dict, order: list[str]):
        self.providers = providers
        self.order = [p for p in order if p in providers]
        self.state = defaultdict(RateState)
        self.lock = asyncio.Lock()

    def candidates(self, override=None):
        if override:
            r = resolve(override)
            return [r] if r and r in self.providers else []
        return list(self.order)

    def pick(self, est_tokens, candidates, required_caps: list[str] | None = None):
        attempts = []
        for name in candidates:
            limits = LIMITS[name]
            prov = self.providers[name]
            caps = getattr(prov, "capabilities", {})
            if required_caps:
                missing = [c for c in required_caps if not caps.get(c)]
                if missing:
                    attempts.append({"provider": name, "reason": f"skipped:no_{missing[0]}"})
                    continue
            if est_tokens > limits["max_ctx"]:
                attempts.append({"provider": name, "reason": f"prompt {est_tokens} > max_ctx {limits['max_ctx']}"})
                continue
            ok, why = self.state[name].can_use(limits, est_tokens)
            if ok:
                return name, attempts
            attempts.append({"provider": name, "reason": why})
        return None, attempts

    def all_status(self):
        out = {}
        for name in self.providers:
            out[name] = self.state[name].snapshot(LIMITS[name])
            out[name]["model"] = self.providers[name].model
            out[name]["capabilities"] = getattr(self.providers[name], "capabilities", {})
        return out


# -----------------------------------------------------------------------------
# V3 Router pool — separate failover ring for routing-decision LLM calls.
# Same rate-state machinery, separate state dict so router quotas never compete
# with worker quotas (provider keys are shared but providers meter per-model).
# -----------------------------------------------------------------------------

DEFAULT_ROUTER_ORDER = ["cerebras", "groq", "nvidia", "github"]


class RouterPool:
    """Failover ring for router-LLM calls. Mirrors `Router` but for the
    Perception/Memory/Decision routing classifiers. Each call is logged with
    a call_role marker (router_perception | router_memory | router_decision)
    so the dashboard can show router activity separately from worker activity.
    """
    def __init__(self, providers: dict, order: list[str]):
        self.providers = providers
        self.order = [p for p in order if p in providers]
        self.state = defaultdict(RateState)
        self.lock = asyncio.Lock()

    def candidates(self):
        return list(self.order)

    def pick(self, est_tokens=400):
        """Pick first available router provider. Caps require nothing — router
        LLMs only need to emit one word, no tools/reasoning/structured needed."""
        attempts = []
        for name in self.candidates():
            limits = LIMITS[name]
            ok, why = self.state[name].can_use(limits, est_tokens)
            if ok:
                return name, attempts
            attempts.append({"provider": name, "reason": why})
        return None, attempts

    def all_status(self):
        out = {}
        for name in self.providers:
            out[name] = self.state[name].snapshot(LIMITS[name])
            out[name]["model"] = self.providers[name].model
        return out
