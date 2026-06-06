# AGENTS.md

Canonical shared guidance for agents working in this repo. Codex reads this
directly; Claude Code imports it through `CLAUDE.md`. Keep Claude-only notes in
`CLAUDE.md`; everything tool-agnostic lives here.

For what the project *is* and a full walkthrough, see [README.md](README.md).
This file is the operational quick-reference for changing the code.

## Layout (two processes)

- `code/` — the agent orchestrator. Entry point `flow.py` (growing-DAG
  executor). Skills are dispatched in `skills.py`; data models live in
  `schemas.py`; FAISS memory in `memory.py`; failure handling in `recovery.py`.
- `code/browser/` — the Session 9 Browser skill (four-layer cascade).
- `code/prompts/*.md` — one system-prompt template per skill.
- `code/agent_config.yaml` — the skill registry (see Conventions).
- `llm_gatewayV9/` — FastAPI LLM gateway, entry point `main.py`, serves `:8109`.
  The agent calls it for every chat/embed/vision step.

## Commands

Run from the directory shown. Use `uv` only (never `pip`).

    # Gateway (terminal 1) — serves :8109
    cd llm_gatewayV9 && uv run main.py

    # Agent (terminal 2)
    cd code && uv sync
    uv run python flow.py "<query>"

    # One-time: browser engine for the Browser skill
    cd code && uv run playwright install chromium

    # Tests
    cd code && uv run pytest

    # Curated demos + a state reset
    ./run_demo.sh            # pytest + 5 canonical queries
    ./run_demo.sh browser    # Browser skill end-to-end
    ./run_demo.sh wipe       # clear state/sessions + logs

    # Inspect a finished run
    cd code && uv run python replay.py <session_id>

## Conventions

- **Adding a skill = data, not code.** A skill is one entry in
  `agent_config.yaml` (`prompt:`, `tools_allowed`, `temperature`, `max_tokens`,
  `description`; optional `internal_successors`, `critic: true`) plus the
  referenced `prompts/*.md`. There is no Python class per skill — `skills.py`
  dispatches generically. Don't add a skill by editing the executor.
- **Typed boundaries.** Skill I/O flows through the Pydantic models in
  `schemas.py` (`AgentResult`, `NodeSpec`, `NodeState`, …). Extend those models
  rather than passing loose dicts.
- **Inputs are references.** Prompts resolve `USER_QUERY`, `n:<node-id>`, and
  `art:<artifact-id>` placeholders (see `skills.py`); follow that pattern for
  new wiring instead of hard-coding values.

## Gotchas & guardrails

- The agent needs the gateway on `:8109`. `code/gateway.py` auto-starts it if
  it's down, but a stale or wrong-port gateway will fail confusingly — check it
  first when LLM calls error.
- Credentials live in a single `.env` at the repo root: at least one provider
  key, plus optional `TAVILY_API_KEY` (the agent falls back to DuckDuckGo
  without it). Provider/key/model matrix: `llm_gatewayV9/README.md`.
- `state/`, `sandbox/`, `usage.json`, and `.env*` are gitignored runtime data
  (see `code/.gitignore`). They regenerate per run — never commit them; reset
  with `./run_demo.sh wipe`.
- Trust the **code** over `llm_gatewayV9/README.md` on ports/versions: that doc
  still references older V3/V7 ports (8101/8107), but this repo runs V9 on
  `:8109`.
- This file inherits the user-level global guidance (uv, truthfulness,
  validation, change discipline). Don't restate it here.
