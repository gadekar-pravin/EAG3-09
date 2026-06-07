# EAGV3 — Session 9: Browser-Capable Agent

A multi-agent orchestrator that decomposes a user query into a **growing
DAG** of skills (Planner, Researcher, Browser, Distiller, Critic, Formatter,
Coder, …) and executes it node-by-node, backed by a custom **LLM gateway** that
auto-routes each request to the cheapest capable provider by token size.

Session 9 adds the headline feature — a **Browser skill** that interacts with
real web pages through a four-layer cost cascade, escalating to a vision model
only when cheaper layers fail.

> This is a course deliverable (EAGV3). It runs entirely on free-tier LLM
> providers and stores all state on local disk.

## How it fits together

The repo is **two processes** that talk over HTTP:

| Process | Path | Role |
|---------|------|------|
| **Agent orchestrator** | [`code/`](code/) | Reads a query, builds and executes a NetworkX DAG of skills. Entry point: [`flow.py`](code/flow.py). |
| **LLM gateway (V9)** | [`llm_gatewayV9/`](llm_gatewayV9/) | FastAPI service on **`:8109`**. Routes `/v1/chat`, `/v1/embed`, and `/v1/vision` calls across seven worker providers. Entry point: [`main.py`](llm_gatewayV9/main.py). |

The agent calls the gateway for every LLM/embedding/vision step. If the gateway
isn't already running, [`code/gateway.py`](code/gateway.py) auto-starts it on
`:8109` on first use.

```
EAG3-09/
├── code/                  # the orchestrator
│   ├── flow.py            # main: growing-graph executor + scheduler
│   ├── skills.py          # skill dispatch + input resolution
│   ├── agent_config.yaml  # skill registry (prompts, tools, per-skill settings)
│   ├── prompts/           # one system-prompt template per skill (*.md)
│   ├── browser/           # Session 9 Browser skill (4-layer cascade)
│   ├── memory.py          # FAISS-backed vector memory
│   ├── recovery.py        # failure classification + recovery decisions
│   ├── persistence.py     # per-session DAG + node snapshots
│   ├── replay.py          # pretty-print a finished session
│   └── state/             # runtime state (sessions, artifacts, FAISS index)
├── llm_gatewayV9/         # the gateway (see its own README for provider matrix)
├── docs/session-09.md     # architecture + assignment notes
├── logs/                  # demo output logs
└── run_demo.sh            # curated demo runner
```

## Architecture

The agent grew across sessions: single agent with cognitive roles (S6) → add
semantic vector memory (S7) → replace the fixed loop with a **growing DAG** and
a skills catalogue (S8) → add the **Browser skill + vision gateway** (S9).

The Planner emits an initial DAG of skill nodes; the executor runs ready nodes
in topological order, and the graph grows at runtime from planner successors,
static skill successors (e.g. Coder → SandboxExecutor), and auto-inserted Critic
nodes. On failure, the Planner is re-invoked to splice in a recovery subgraph.

### The four-layer Browser cascade

The Browser skill picks the cheapest way to do each job and only spends more
when the cheap way fails:

| Layer | Method | Cost | Use case |
|-------|--------|------|----------|
| **1 — Extract** | `httpx` + `trafilatura` (no browser, no LLM) | $0 | Static content sites (blogs, docs). |
| **2a — Deterministic** | Playwright + hand-written CSS selectors (no LLM) | $0 | Sites with known-stable DOM. |
| **2b — A11y tree** | Playwright + accessibility tree + cheap text LLM | ~tiny | Dynamic pages with filters/dropdowns. |
| **3 — Vision** | Playwright + set-of-marks screenshot + VLM | per-call vision cost | JS-heavy pages with no useful a11y tree. |

Above all four sits a precondition check: if a CAPTCHA, login wall, geo-block,
or rate limit stops the page from rendering, the skill returns
`error_code="gateway_blocked"` so the orchestrator can re-route. See
[`docs/session-09.md`](docs/session-09.md) for the full design.

## Prerequisites

- **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/)
- **Playwright Chromium** (for the Browser skill):
  ```bash
  cd code && uv run playwright install chromium
  ```
- A **`.env`** at the repo root with at least one LLM provider key, plus an
  optional `TAVILY_API_KEY` for web search (the agent falls back to DuckDuckGo
  without it). See [`llm_gatewayV9/README.md`](llm_gatewayV9/README.md) for the
  full provider/key/model matrix — the gateway routes across seven workers and
  fails over automatically.

## Setup & run

Two terminals:

```bash
# Terminal 1 — start the gateway (serves :8109)
cd llm_gatewayV9
uv run main.py
# or:
./run.sh

# Terminal 2 — run the agent on a query
cd code
uv sync
uv run python flow.py "When was Claude Shannon born and when did he die?"
```

> You can skip Terminal 1 — `code/gateway.py` auto-starts the gateway on `:8109`
> if it isn't already up. Running it yourself just makes the gateway logs and
> dashboard easy to watch.

## Demos

[`run_demo.sh`](run_demo.sh) runs curated queries that each exercise one
orchestrator feature. Each query's stdout is teed to `logs/<slug>.log`, and the
script prints the session id plus a one-liner to inspect any node's rendered
prompt.

Run demo commands from the repo root:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09
```

```bash
./run_demo.sh              # pytest + the 5 canonical queries
./run_demo.sh tests        # unit tests only
./run_demo.sh hello        # smallest DAG: planner → formatter
./run_demo.sh shannon      # single-item query (USER_QUERY flow)
./run_demo.sh populations  # parallel fan-out (per-worker scoping)
./run_demo.sh structured   # forces distiller → auto-critic chain
./run_demo.sh fail         # graceful fail-by-planning
./run_demo.sh browser      # Session 9 Browser skill end-to-end
./run_demo.sh wipe         # clear state/sessions + logs
```

The default `./run_demo.sh` run covers the non-browser path: unit tests, then
`hello`, `shannon`, `populations`, `structured`, and `fail`. The Browser demo is
separate because it needs Playwright Chromium and exercises the Session 9
Browser skill end-to-end:

```bash
cd code
uv run playwright install chromium   # one-time setup for browser demos
cd ..
./run_demo.sh browser
```

Each run writes its log to `logs/<slug>.log` and its full DAG + per-node
snapshots to `code/state/sessions/<session_id>/`.

If your terminal says `zsh: no such file or directory: ./run_demo.sh`, you are
not in the repo root. Run:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09
./run_demo.sh
```

Common terminal flow for live demos:

```bash
# Terminal 1
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/llm_gatewayV9
./run.sh

# Terminal 2
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09
./run_demo.sh
# or, for the Browser skill:
./run_demo.sh browser
```

## Testing

```bash
cd code
uv run pytest          # recovery, recovery-amnesia, critic-autoinsert, vision-search
```

## Inspecting a session

Replay a finished session's DAG and node outputs in completion order:

```bash
cd code
uv run python replay.py <session_id>
```

To see the exact prompt sent for a given node:

```bash
python3 -c "import json; print(json.load(open('state/sessions/<session_id>/nodes/n_001.json'))['prompt_sent'])"
```

## Further reading

- [`docs/session-09.md`](docs/session-09.md) — architecture and assignment notes
- [`code/VALIDATION.md`](code/VALIDATION.md) — Session 9 integration checklist
- [`llm_gatewayV9/README.md`](llm_gatewayV9/README.md) — gateway spec, endpoints, provider matrix
- [`AGENTS.md`](AGENTS.md) — shared guidance for coding agents in this repo
