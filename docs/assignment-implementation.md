# Session 9 Assignment Implementation Notes

This document explains the assignment in `code/assignment.md`, how the current
code satisfies each requirement, and how to test the assignment-specific
changes.

## Assignment Summary

The assignment asks for a browser-capable agent that completes a real web
comparison task and produces a replayable report of the run.

The required task is:

> Compare top 3 Hugging Face text-generation models sorted by likes.

The important constraints are:

- The answer must come from browser interaction, not passive search snippets.
- The agent must perform at least three visible browser actions.
- The final answer must include a structured comparison table.
- Replay/report output must show the original goal, Planner DAG, Browser path,
  Browser actions, screenshots or page-state logs, extracted data, final table,
  turn count, and cost summary.
- The orchestrator must not be modified. New behavior must plug in through the
  skill catalogue or Browser skill extension.

## How The Code Satisfies The Requirements

| Assignment requirement | Implementation |
|---|---|
| Compare top 3 Hugging Face text-generation models sorted by likes | `run_demo.sh browser` now uses the exact assignment query. `prompts/planner.md` has an assignment-critical route that forces the Browser node to use `https://huggingface.co/models` as the base URL and puts Text Generation plus Most Likes in `metadata.goal`. |
| Use browser interaction, not snippets | `code/browser/recipes.py` adds a Hugging Face browser recipe that drives the rendered Hugging Face page with Playwright-visible actions, then extracts rendered model cards. `code/browser/skill.py` invokes this recipe before falling back to the generic cascade. |
| At least three visible browser actions | The Hugging Face recipe records three actions: click Text Generation, open the Sort menu, and click Most Likes. These are returned in `BrowserOutput.actions`. |
| Structured comparison table | `prompts/distiller.md` now tells Distiller to emit exactly three Hugging Face model records when the Browser output contains them. `prompts/formatter.md` now tells Formatter to render a Markdown table with Rank, Model, Likes, Downloads, Description, and URL. |
| Replay/report checklist | `code/replay.py` adds `--report`, which prints the eight assignment sections in Markdown. |
| Browser path includes blocked | `BrowserOutput.path` in `code/schemas.py` now accepts `blocked`. Gateway-blocked Browser failures return `path="blocked"` plus `error_code="gateway_blocked"`. |
| Screenshots or page-state logs | The Hugging Face recipe writes per-step screenshots and text state logs under the session Browser artifact directory and surfaces those paths in `BrowserOutput.page_state_logs`. |
| Extracted data | The recipe stores parsed cards under `BrowserOutput.extracted_data["models"]`, and also formats them into Browser content for downstream Distiller/Formatter nodes. |
| Turn count and cost summary | Browser turns come from `BrowserOutput.turns`; report mode also queries V9 `/v1/cost/by_agent?session=<session_id>` for per-agent calls, tokens, and dollars. |
| No orchestrator modification | `code/flow.py` is unchanged. The work is contained in Browser skill extension code, typed schema additions, prompts, replay/reporting, demo wiring, and tests. |

## Main Files Changed

- `code/browser/recipes.py`
  - New deterministic recipe for the assignment's Hugging Face model-listing
    task.
  - Parses rendered Hugging Face model cards into rank, model id, likes,
    downloads, description, and URL.
  - Records visible browser actions and page-state artifacts.

- `code/browser/skill.py`
  - Detects the Hugging Face assignment target and runs the deterministic
    recipe before the generic Browser cascade.
  - Preserves fallback behavior: if the recipe cannot extract three cards, the
    normal extract/deterministic/a11y/vision cascade still runs.
  - Returns blocked pages as `path="blocked"`.

- `code/schemas.py`
  - Extends `BrowserOutput.path` with `blocked`.
  - Adds optional report fields: `artifacts_dir`, `page_state_logs`, and
    `extracted_data`.

- `code/replay.py`
  - Adds `uv run python replay.py --report <session_id>`.
  - Keeps existing interactive replay behavior unchanged.

- `code/prompts/planner.md`
  - Adds the assignment-critical route so this task uses Browser, not memory or
    web-search snippets.

- `code/prompts/distiller.md` and `code/prompts/formatter.md`
  - Add Hugging Face table-specific extraction and formatting instructions.

- `run_demo.sh`
  - Updates the `browser` demo to the exact assignment query.

- `code/tests/test_assignment_browser_report.py`
  - Adds regression coverage for the Browser blocked path, Hugging Face card
    extraction, replay report sections, and Planner prompt guardrails.

## Test And Verification Commands

Run commands from the repo root unless a command says otherwise.

### 1. Static And Unit-Test Verification

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/code
uv run pytest tests/ -q
```

Expected result:

```text
45 passed
```

This covers the assignment-specific tests plus the existing curated regression
tests under `code/tests/`.

You can run only the assignment-specific tests with:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/code
uv run pytest tests/test_assignment_browser_report.py -q
```

Expected result:

```text
4 passed
```

Optional compile check:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/code
uv run python -m py_compile browser/recipes.py browser/skill.py replay.py schemas.py
```

No output means the files compile.

### 2. Whitespace / Patch Hygiene

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09
git diff --check
```

Expected result: no output.

### 3. Live Browser Assignment Run

Start the V9 gateway in one terminal:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/llm_gatewayV9
uv run main.py
```

In a second terminal, run the Browser demo:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09
./run_demo.sh browser
```

The demo query should be:

```text
Compare top 3 Hugging Face text-generation models sorted by likes. Return a structured comparison table.
```

After the run, `run_demo.sh` prints the session directory. Capture the session
id, then generate the assignment report:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/code
uv run python replay.py --report <session_id>
```

### 4. Live Run Acceptance Checklist

The live run is assignment-complete when the report shows:

- `## 1. Original User Goal`
  - Contains the exact Hugging Face top-3 assignment query.
- `## 2. Planner DAG`
  - Shows Planner, Browser, Distiller, Critic, and Formatter nodes. The Critic
    is auto-inserted after Distiller by existing orchestration behavior.
- `## 3. Browser Path Chosen`
  - Shows `deterministic` for the Hugging Face recipe, or `a11y` if the recipe
    falls back and the generic cascade completes.
- `## 4. Browser Actions Taken`
  - Includes at least three actions, corresponding to Text Generation, Sort,
    and Most Likes.
- `## 5. Screenshots Or Page-State Logs`
  - Lists paths under `code/state/sessions/<session_id>/browser/...`.
- `## 6. Extracted Data`
  - Contains exactly three model records under `models`.
- `## 7. Final Comparison Table`
  - Contains a Markdown table with three rows.
- `## 8. Turn Count And Cost Summary`
  - Shows Browser turns and gateway cost-by-agent data.

### 5. Troubleshooting Live Validation

If the gateway is not reachable:

```bash
curl -s --max-time 5 http://localhost:8109/v1/status
```

If this fails, start the gateway from `llm_gatewayV9` as shown above. If gateway
startup reports that port `8109` is already in use but `curl` still cannot
connect, there may be a stale listener. Stop the stale process or restart the
terminal environment, then start the gateway again.

If Playwright Chromium is missing:

```bash
cd /Users/pravingadekar/Documents/EAG3/EAG3-09/EAG3-09/code
uv run playwright install chromium
```

If Hugging Face changes its DOM and the deterministic recipe cannot extract
three cards, the Browser skill falls back to the generic cascade. The report
should still expose the path taken, the actions attempted, and whether the final
data was complete.

## Verification Already Performed

During implementation, the following checks passed:

```bash
cd code
uv run pytest tests/ -q
uv run python -m py_compile browser/recipes.py browser/skill.py replay.py schemas.py
cd ..
git diff --check
```

The curated test suite passed with `45 passed`. A live Browser run was not
performed in that pass because `:8109` reported a bind conflict while HTTP
probes to the gateway refused connections.
