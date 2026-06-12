"""Replay a persisted Session 8 run, one node at a time.

Stdin-driven. Reads `state/sessions/<sid>/` and walks its NodeState
records in completion order. For each node prints a fixed block, then
waits for the user to advance.

Usage:
    uv run python replay.py <session_id>

Keys:
    enter   advance to next node
    p       expand the full rendered prompt that was sent to the gateway
    o       expand the full AgentResult.output JSON
    q       quit
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import httpx
import networkx as nx

from persistence import SessionStore, list_sessions
from schemas import NodeState


def _print_block(i: int, n: int, st: NodeState) -> None:
    r = st.result
    skill = st.skill
    elapsed = f"{r.elapsed_s:.1f}s" if r and r.elapsed_s else "—"
    provider = (r.provider if r and r.provider else "—")
    retries = st.retries
    tools = ""
    print()
    print(f"node {i} / {n}")
    print(f"  agent      {skill}")
    print(f"  status     {st.status}")
    print(f"  elapsed    {elapsed}")
    print(f"  provider   {provider}")
    print(f"  retries    {retries}")
    print(f"  inputs     {', '.join(st.inputs) or '(none)'}")
    if tools:
        print(f"  tools      {tools}")
    if r and r.error:
        print(f"  error      {r.error[:240]}")
    if r and r.output:
        try:
            out_preview = json.dumps(r.output, ensure_ascii=False)
        except (TypeError, ValueError):
            out_preview = str(r.output)
        if len(out_preview) > 500:
            out_preview = out_preview[:500] + "…"
        print(f"  output     {out_preview}")


def _expand_prompt(st: NodeState) -> None:
    print()
    print("─" * 78)
    print(st.prompt_sent or "(no prompt captured)")
    print("─" * 78)


def _expand_output(st: NodeState) -> None:
    print()
    print("─" * 78)
    if st.result and st.result.output:
        print(json.dumps(st.result.output, indent=2, ensure_ascii=False))
    else:
        print("(no output)")
    print("─" * 78)


def replay(session_id: str) -> int:
    store = SessionStore(session_id)
    states = store.read_all_nodes()
    if not states:
        print(f"replay: no nodes under state/sessions/{session_id}/", file=sys.stderr)
        return 2

    query = store.read_query() or ""
    print(f"session  {session_id}")
    print(f"query    {query[:200]}")
    print(f"nodes    {len(states)}")
    print()
    print("press enter to advance, p to expand prompt, o to expand output, q to quit")

    i = 0
    while i < len(states):
        st = states[i]
        _print_block(i + 1, len(states), st)
        try:
            cmd = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if cmd == "q":
            return 0
        if cmd == "p":
            _expand_prompt(st)
            continue
        if cmd == "o":
            _expand_output(st)
            continue
        i += 1
    print("\n(end of session)")
    return 0


def _node_label(st: NodeState) -> str:
    return f"{st.node_id} {st.skill} [{st.status}]"


def _format_actions(actions: list[dict]) -> str:
    if not actions:
        return "(none recorded)"
    lines = []
    for step in actions:
        turn = step.get("turn", "?")
        outcome = step.get("outcome", "")
        bits = []
        for action in step.get("actions", []) or []:
            kind = action.get("type", "action")
            label = action.get("label") or action.get("selector") or action.get("mark") or ""
            value = action.get("value")
            if value:
                label = f"{label}={value}" if label else str(value)
            bits.append(f"{kind} {label}".strip())
        lines.append(f"- turn {turn}: {', '.join(bits) or '(no action)'} -> {outcome or 'ok'}")
    return "\n".join(lines)


def _format_dag(store: SessionStore, states: list[NodeState]) -> str:
    try:
        graph = store.read_graph()
    except Exception:  # noqa: BLE001 - replay must tolerate partial sessions
        graph = None
    if graph is None:
        return "\n".join(f"- {_node_label(st)}" for st in states)
    lines = []
    for nid in graph.nodes:
        data = graph.nodes[nid]
        lines.append(f"- {nid}: {data.get('skill')} [{data.get('status')}]")
    edges = [f"{u} -> {v}" for u, v in graph.edges]
    if edges:
        lines.append("")
        lines.append("Edges:")
        lines.extend(f"- {edge}" for edge in edges)
    return "\n".join(lines)


def _fetch_cost_summary(session_id: str, *, gateway_url: str = "http://localhost:8109") -> str:
    try:
        r = httpx.get(
            f"{gateway_url.rstrip('/')}/v1/cost/by_agent",
            params={"session": session_id},
            timeout=3,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return f"Gateway cost summary unavailable: {type(e).__name__}: {e}"
    if not data:
        return "(no gateway ledger rows for this session)"
    lines = []
    for agent, rows in sorted(data.items()):
        for row in rows:
            calls = row.get("calls", 0)
            in_tok = row.get("in_tok", 0)
            out_tok = row.get("out_tok", 0)
            dollars = row.get("dollars", 0)
            provider = row.get("provider", "")
            lines.append(
                f"- {agent}/{provider}: calls={calls}, tokens={in_tok}/{out_tok}, "
                f"dollars=${float(dollars):.6f}"
            )
    return "\n".join(lines)


def _result_output(st: NodeState | None) -> dict:
    if not st or not st.result or not isinstance(st.result.output, dict):
        return {}
    return st.result.output


def _assignment_report_parts(
    session_id: str, *, gateway_url: str = "http://localhost:8109"
) -> dict:
    store = SessionStore(session_id)
    states = store.read_all_nodes()
    if not states:
        raise RuntimeError(f"replay: no nodes under state/sessions/{session_id}/")

    query = store.read_query() or ""
    browser = next((st for st in states if st.skill == "browser"), None)
    distiller = next((st for st in states if st.skill == "distiller"), None)
    formatter = next((st for st in reversed(states) if st.skill == "formatter"), None)
    critic = next((st for st in reversed(states) if st.skill == "critic"), None)

    browser_out = _result_output(browser)
    formatter_out = _result_output(formatter)
    distiller_out = _result_output(distiller)
    final_answer = formatter_out.get("final_answer", "")
    extracted = browser_out.get("extracted_data") or {}
    if not extracted and distiller_out:
        extracted = distiller_out.get("fields", {}) or distiller_out

    total_turns = sum(
        int((st.result.output or {}).get("turns") or 0)
        for st in states
        if st.result and isinstance(st.result.output, dict)
    )

    return {
        "store": store,
        "states": states,
        "query": query,
        "browser": browser,
        "browser_out": browser_out,
        "critic": critic,
        "critic_out": _result_output(critic),
        "extracted": extracted,
        "final_answer": final_answer,
        "total_turns": total_turns,
        "cost_summary": _fetch_cost_summary(session_id, gateway_url=gateway_url),
    }


def build_report(session_id: str, *, gateway_url: str = "http://localhost:8109") -> str:
    parts = _assignment_report_parts(session_id, gateway_url=gateway_url)
    store = parts["store"]
    states = parts["states"]
    query = parts["query"]
    browser_out = parts["browser_out"]
    final_answer = parts["final_answer"]
    extracted = parts["extracted"]

    artifact_lines = []
    if browser_out.get("artifacts_dir"):
        artifact_lines.append(f"- artifacts_dir: {browser_out['artifacts_dir']}")
    for path in browser_out.get("page_state_logs", []) or []:
        artifact_lines.append(f"- {path}")
    if not artifact_lines:
        artifact_lines.append("(none recorded)")

    return "\n".join([
        "# Browser Comparison Replay Report",
        "",
        "## 1. Original User Goal",
        query or "(missing query)",
        "",
        "## 2. Planner DAG",
        _format_dag(store, states),
        "",
        "## 3. Browser Path Chosen",
        str(browser_out.get("path") or "(no browser node)"),
        "",
        "## 4. Browser Actions Taken",
        _format_actions(browser_out.get("actions", []) or []),
        "",
        "## 5. Screenshots Or Page-State Logs",
        "\n".join(artifact_lines),
        "",
        "## 6. Extracted Data",
        json.dumps(extracted or {}, indent=2, ensure_ascii=False),
        "",
        "## 7. Final Comparison Table",
        final_answer or "(no formatter final_answer)",
        "",
        "## 8. Turn Count And Cost Summary",
        f"Browser turns: {browser_out.get('turns', 0)}",
        f"Total browser/action turns across nodes: {parts['total_turns']}",
        parts["cost_summary"],
    ])


def _html_text(value: object) -> str:
    return html.escape(str(value), quote=True)


def _artifact_uri(path: str) -> str:
    try:
        p = Path(path).expanduser()
        if p.is_absolute():
            return p.as_uri()
    except (OSError, ValueError):
        pass
    return path


def _html_link(path: str, label: str | None = None) -> str:
    href = _html_text(_artifact_uri(path))
    text = _html_text(label or path)
    return f'<a href="{href}">{text}</a>'


def _render_markdown_links(text: str) -> str:
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    out: list[str] = []
    pos = 0
    for match in pattern.finditer(text):
        out.append(_html_text(text[pos:match.start()]))
        label = _html_text(match.group(1))
        href = _html_text(match.group(2))
        out.append(f'<a href="{href}">{label}</a>')
        pos = match.end()
    out.append(_html_text(text[pos:]))
    return "".join(out)


def _render_markdown_table(markdown: str) -> str:
    lines = markdown.splitlines()
    table_start = None
    for i in range(len(lines) - 1):
        if "|" not in lines[i] or "|" not in lines[i + 1]:
            continue
        cells = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            table_start = i
            break
    if table_start is None:
        return f"<p>{_render_markdown_links(markdown).replace(chr(10), '<br>')}</p>"

    table_end = table_start + 2
    while table_end < len(lines) and "|" in lines[table_end]:
        table_end += 1

    before = "\n".join(line for line in lines[:table_start] if line.strip())
    after = "\n".join(line for line in lines[table_end:] if line.strip())
    header = [c.strip() for c in lines[table_start].strip().strip("|").split("|")]
    body_rows = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in lines[table_start + 2:table_end]
    ]

    chunks: list[str] = []
    if before:
        chunks.append(f"<p>{_render_markdown_links(before).replace(chr(10), '<br>')}</p>")
    chunks.append("<table><thead><tr>")
    chunks.extend(f"<th>{_render_markdown_links(cell)}</th>" for cell in header)
    chunks.append("</tr></thead><tbody>")
    for row in body_rows:
        chunks.append("<tr>")
        padded = row + [""] * max(0, len(header) - len(row))
        chunks.extend(f"<td>{_render_markdown_links(cell)}</td>" for cell in padded[:len(header)])
        chunks.append("</tr>")
    chunks.append("</tbody></table>")
    if after:
        chunks.append(f"<p>{_render_markdown_links(after).replace(chr(10), '<br>')}</p>")
    return "\n".join(chunks)


def _status_class(status: object) -> str:
    s = str(status or "").lower()
    if s in {"complete", "completed", "pass", "success", "ok", "done"}:
        return "ok"
    if s in {"failed", "fail", "error", "blocked"}:
        return "bad"
    return "neutral"


def _status_pill(status: object) -> str:
    text = str(status or "-")
    return f'<span class="pill {_status_class(text)}">{_html_text(text)}</span>'


def _svg_dag(graph: "nx.DiGraph") -> str:
    cols = [sorted(gen) for gen in nx.topological_generations(graph)]
    if not cols:
        raise ValueError("empty graph")
    box_w, box_h, x_gap, y_gap, pad = 150, 52, 72, 26, 14
    n_rows = max(len(col) for col in cols)
    total_h = n_rows * box_h + (n_rows - 1) * y_gap + 2 * pad
    total_w = 2 * pad + len(cols) * box_w + (len(cols) - 1) * x_gap
    pos: dict[str, tuple[float, float]] = {}
    for ci, col in enumerate(cols):
        col_h = len(col) * box_h + (len(col) - 1) * y_gap
        y0 = (total_h - col_h) / 2
        for ri, nid in enumerate(col):
            pos[nid] = (pad + ci * (box_w + x_gap), y0 + ri * (box_h + y_gap))

    parts = [
        f'<svg class="dag" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
        'role="img" aria-label="Planner DAG">',
        '<defs><marker id="dag-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 1 L 9 5 L 0 9 z"/></marker></defs>',
    ]
    for u, v in graph.edges:
        x1, y1 = pos[u]
        x2, y2 = pos[v]
        sx, sy = x1 + box_w, y1 + box_h / 2
        ex, ey = x2, y2 + box_h / 2
        mx = (sx + ex) / 2
        parts.append(
            f'<path class="dag-edge" d="M {sx:.0f} {sy:.0f} '
            f'C {mx:.0f} {sy:.0f}, {mx:.0f} {ey:.0f}, {ex:.0f} {ey:.0f}" '
            'marker-end="url(#dag-arrow)"/>'
        )
    for nid, (x, y) in pos.items():
        data = graph.nodes[nid]
        parts.append(f'<g class="dag-node {_status_class(data.get("status"))}">')
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{box_w}" height="{box_h}" rx="9"/>')
        parts.append(f'<text class="dag-id" x="{x + 14:.0f}" y="{y + 21:.0f}">{_html_text(nid)}</text>')
        parts.append(
            f'<text class="dag-skill" x="{x + 14:.0f}" y="{y + 39:.0f}">'
            f'{_html_text(data.get("skill") or "?")}</text>'
        )
        parts.append(f'<circle class="dag-dot" cx="{x + box_w - 14:.0f}" cy="{y + box_h / 2:.0f}" r="4"/>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def _html_dag(store: SessionStore, states: list[NodeState]) -> str:
    try:
        graph = store.read_graph()
    except Exception:  # noqa: BLE001 - replay must tolerate partial sessions
        graph = None
    if graph is None:
        items = "".join(f"<li>{_html_text(_node_label(st))}</li>" for st in states)
        return f"<ul class=\"node-list\">{items}</ul>"

    try:
        return _svg_dag(graph)
    except Exception:  # noqa: BLE001 - cycles/partial graphs fall back to lists
        pass

    node_items = []
    for nid in graph.nodes:
        data = graph.nodes[nid]
        node_items.append(
            "<li>"
            f"<span class=\"node-id\">{_html_text(nid)}</span>"
            f"<span>{_html_text(data.get('skill'))}</span>"
            f"{_status_pill(data.get('status'))}"
            "</li>"
        )
    edge_items = "".join(
        f"<li>{_html_text(u)} -> {_html_text(v)}</li>"
        for u, v in graph.edges
    )
    edges = f"<h3>Edges</h3><ul>{edge_items}</ul>" if edge_items else ""
    return f"<ul class=\"node-list\">{''.join(node_items)}</ul>{edges}"


def _html_actions(actions: list[dict]) -> str:
    if not actions:
        return "<p class=\"muted\">none recorded</p>"
    items = []
    for step in actions:
        bits = []
        for action in step.get("actions", []) or []:
            kind = action.get("type", "action")
            label = action.get("label") or action.get("selector") or action.get("mark") or ""
            value = action.get("value")
            if value:
                label = f"{label}={value}" if label else str(value)
            bits.append(f"{kind} {label}".strip())
        items.append(
            "<li>"
            f"<span class=\"turn\">{_html_text(step.get('turn', '?'))}</span>"
            f"<span class=\"act\">{_html_text(', '.join(bits) or '(no action)')}</span>"
            f"{_status_pill(step.get('outcome') or 'ok')}"
            "</li>"
        )
    return f"<ol class=\"timeline\">{''.join(items)}</ol>"


def _group_page_state_logs(paths: list[str]) -> list[dict]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for raw in paths:
        path = str(raw)
        suffix = Path(path).suffix.lower()
        key = str(Path(path).with_suffix(""))
        if key not in grouped:
            grouped[key] = {"stem": Path(path).stem, "files": []}
            order.append(key)
        grouped[key]["files"].append(path)
        if suffix == ".png":
            grouped[key]["png"] = path
        elif suffix == ".txt":
            grouped[key]["txt"] = path
    return [grouped[key] for key in order]


def _read_text_log(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError as e:
        return f"(unable to read log: {type(e).__name__}: {e})"


def _html_artifacts(browser_out: dict) -> str:
    paths = [str(p) for p in browser_out.get("page_state_logs", []) or []]
    if not paths and not browser_out.get("artifacts_dir"):
        return "<p class=\"muted\">none recorded</p>"

    chunks: list[str] = []
    if browser_out.get("artifacts_dir"):
        chunks.append(
            f"<p><strong>Artifacts directory:</strong> "
            f"{_html_link(str(browser_out['artifacts_dir']))}</p>"
        )
    groups = _group_page_state_logs(paths)
    if groups:
        chunks.append("<div class=\"artifact-grid\">")
    for group in groups:
        title = _html_text(group["stem"])
        chunks.append("<article class=\"artifact-card\">")
        chunks.append(f"<h3>{title}</h3>")
        if group.get("png"):
            src = _html_text(_artifact_uri(group["png"]))
            chunks.append(
                f'<a href="{src}"><img src="{src}" alt="{title} screenshot"></a>'
            )
        if group.get("txt"):
            log_text = _read_text_log(group["txt"])
            chunks.append(
                "<details><summary>Page-state log</summary>"
                f"<pre>{_html_text(log_text)}</pre></details>"
            )
        links = " ".join(_html_link(path, Path(path).name) for path in group["files"])
        chunks.append(f"<p class=\"artifact-links\">{links}</p>")
        chunks.append("</article>")
    if groups:
        chunks.append("</div>")
    return "\n".join(chunks)


def _html_extracted(extracted: dict) -> str:
    payload = json.dumps(extracted or {}, indent=2, ensure_ascii=False)
    return (
        "<details open><summary>Extracted JSON</summary>"
        f"<pre>{_html_text(payload)}</pre></details>"
    )


def _html_cost_summary(cost_summary: str) -> str:
    if not cost_summary:
        return "<p class=\"muted\">none recorded</p>"
    lines = [line.strip() for line in cost_summary.splitlines() if line.strip()]
    if lines and all(line.startswith("- ") for line in lines):
        items = []
        for line in lines:
            text = line[2:]
            head, sep, dollars = text.rpartition(", dollars=")
            if sep:
                items.append(
                    "<li>"
                    f"<span>{_html_text(head)}</span>"
                    f"<span class=\"cost-dollars\">{_html_text(dollars)}</span>"
                    "</li>"
                )
            else:
                items.append(f"<li><span>{_html_text(text)}</span></li>")
        return f"<ul class=\"cost-list\">{''.join(items)}</ul>"
    return f"<pre>{_html_text(cost_summary)}</pre>"


def _html_node_summaries(states: list[NodeState]) -> str:
    rows = []
    for st in states:
        result = st.result
        elapsed = f"{result.elapsed_s:.1f}s" if result and result.elapsed_s else "-"
        provider = result.provider if result and result.provider else "-"
        error = result.error if result and result.error else ""
        rows.append(
            "<tr>"
            f"<td class=\"mono\">{_html_text(st.node_id)}</td>"
            f"<td>{_html_text(st.skill)}</td>"
            f"<td>{_status_pill(st.status)}</td>"
            f"<td class=\"mono\">{_html_text(elapsed)}</td>"
            f"<td class=\"mono\">{_html_text(provider)}</td>"
            f"<td>{_html_text(error[:160])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Node</th><th>Skill</th><th>Status</th>"
        "<th>Elapsed</th><th>Provider</th><th>Error</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


# Plain string (not an f-string) so CSS braces need no doubling.
_REPORT_CSS = """
    :root {
      color-scheme: dark;
      --bg: #0a0f16;
      --panel: #111927;
      --panel-deep: #0b121d;
      --ink: #e7eef7;
      --muted: #8294a8;
      --line: #1f2c3d;
      --accent: #38e1c3;
      --accent-soft: rgba(56, 225, 195, 0.12);
      --ok: #46d68f;
      --ok-soft: rgba(70, 214, 143, 0.12);
      --bad: #ff7373;
      --bad-soft: rgba(255, 115, 115, 0.12);
      --warn: #f5b945;
      --display: "Chakra Petch", "Avenir Next Condensed", system-ui, sans-serif;
      --body: "IBM Plex Sans", system-ui, -apple-system, sans-serif;
      --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: var(--body);
      line-height: 1.55;
      background:
        linear-gradient(rgba(56, 225, 195, 0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(56, 225, 195, 0.03) 1px, transparent 1px),
        radial-gradient(1100px 500px at 75% -10%, rgba(56, 225, 195, 0.07), transparent 60%),
        var(--bg);
      background-size: 36px 36px, 36px 36px, auto, auto;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(13, 20, 31, 0.6), rgba(10, 15, 22, 0.92));
      padding: 40px max(24px, calc((100vw - 1120px) / 2)) 0;
    }
    .eyebrow {
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.28em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 0 0 10px;
    }
    h1, h2, h3 { font-family: var(--display); margin-top: 0; line-height: 1.15; }
    h1 { font-size: 34px; letter-spacing: 0.01em; margin-bottom: 6px; }
    h2 { font-size: 19px; letter-spacing: 0.04em; text-transform: uppercase; }
    h2::after {
      content: "";
      display: block;
      width: 44px;
      height: 2px;
      margin-top: 8px;
      background: var(--accent);
      opacity: 0.8;
    }
    h3 { font-size: 13px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
    .session-line { color: var(--muted); margin: 0 0 18px; }
    .session-line strong { font-family: var(--mono); color: var(--ink); font-weight: 600; }
    .stats { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 22px; }
    .stat {
      display: inline-flex;
      align-items: center;
      gap: 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(17, 25, 39, 0.7);
      padding: 8px 14px;
    }
    .stat-label {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .stat-value { font-family: var(--mono); font-size: 16px; font-weight: 600; }
    .toc { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 14px 0; border-top: 1px solid var(--line); }
    .toc a {
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.06em;
      color: var(--muted);
      text-decoration: none;
    }
    .toc a:hover { color: var(--accent); }
    .toc a span { color: var(--accent); margin-right: 6px; }
    main { width: min(1120px, calc(100vw - 32px)); margin: 28px auto 64px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 20px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03), 0 10px 30px rgba(2, 6, 12, 0.45);
      animation: rise 0.55s ease both;
    }
    section:nth-of-type(2) { animation-delay: 0.05s; }
    section:nth-of-type(3) { animation-delay: 0.1s; }
    section:nth-of-type(4) { animation-delay: 0.15s; }
    section:nth-of-type(5) { animation-delay: 0.2s; }
    section:nth-of-type(6) { animation-delay: 0.25s; }
    section:nth-of-type(7) { animation-delay: 0.3s; }
    section:nth-of-type(8) { animation-delay: 0.35s; }
    @keyframes rise {
      from { opacity: 0; transform: translateY(14px); }
      to { opacity: 1; transform: none; }
    }
    @media (prefers-reduced-motion: reduce) {
      html { scroll-behavior: auto; }
      section { animation: none; }
      .artifact-card { transition: none; }
    }
    a { color: var(--accent); }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 14px;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid rgba(56, 225, 195, 0.35);
      font-family: var(--mono);
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-size: 13px;
    }
    .muted { color: var(--muted); }
    .mono { font-family: var(--mono); font-size: 13px; }
    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 10px;
      font-family: var(--mono);
      font-size: 12px;
      font-weight: 500;
      border: 1px solid var(--line);
      color: var(--muted);
      background: rgba(255, 255, 255, 0.02);
    }
    .pill.ok { color: var(--ok); border-color: rgba(70, 214, 143, 0.4); background: var(--ok-soft); }
    .pill.bad { color: var(--bad); border-color: rgba(255, 115, 115, 0.4); background: var(--bad-soft); }
    .node-list { padding-left: 0; list-style: none; }
    .node-list li {
      display: grid;
      grid-template-columns: 92px 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 9px 0;
      border-bottom: 1px solid var(--line);
    }
    .node-id { font-family: var(--mono); }
    .dag { display: block; width: 100%; max-width: 980px; height: auto; margin: 6px 0 18px; }
    .dag-node rect { fill: var(--panel-deep); stroke: var(--line); stroke-width: 1.4; }
    .dag-node.ok rect { stroke: rgba(70, 214, 143, 0.55); }
    .dag-node.bad rect { stroke: rgba(255, 115, 115, 0.6); }
    .dag-id { font-family: var(--mono); font-size: 13px; font-weight: 600; fill: var(--ink); }
    .dag-skill { font-family: var(--mono); font-size: 11px; fill: var(--muted); letter-spacing: 0.04em; }
    .dag-dot { fill: var(--muted); }
    .dag-node.ok .dag-dot { fill: var(--ok); }
    .dag-node.bad .dag-dot { fill: var(--bad); }
    .dag-edge { fill: none; stroke: #33465e; stroke-width: 1.6; }
    #dag-arrow path { fill: #33465e; }
    table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }
    th, td { padding: 10px 12px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
    th {
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      border-bottom-color: #2a3a50;
    }
    tbody tr:nth-child(even) { background: rgba(255, 255, 255, 0.02); }
    tbody tr:hover { background: var(--accent-soft); }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #070b11;
      color: #d9e4f1;
      border: 1px solid var(--line);
      font-family: var(--mono);
      font-size: 13px;
      padding: 16px;
      border-radius: 8px;
      overflow: auto;
    }
    .timeline { list-style: none; padding: 0; margin: 0; position: relative; }
    .timeline::before {
      content: "";
      position: absolute;
      left: 16px;
      top: 10px;
      bottom: 10px;
      width: 1px;
      background: var(--line);
    }
    .timeline li {
      display: grid;
      grid-template-columns: 34px 1fr auto;
      gap: 14px;
      align-items: center;
      padding: 9px 0;
      position: relative;
    }
    .turn {
      width: 33px;
      height: 33px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-family: var(--mono);
      font-size: 12px;
      font-weight: 600;
      color: var(--accent);
      background: var(--panel-deep);
      border: 1px solid var(--line);
      position: relative;
      z-index: 1;
    }
    .act { overflow-wrap: anywhere; }
    .artifact-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-top: 14px;
    }
    .artifact-card {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-deep);
      padding: 16px;
      margin: 0;
      transition: transform 0.18s ease, border-color 0.18s ease;
    }
    .artifact-card:hover { transform: translateY(-2px); border-color: rgba(56, 225, 195, 0.35); }
    .artifact-card img {
      display: block;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      margin: 10px 0;
    }
    .artifact-links { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 0; }
    .artifact-links a {
      font-family: var(--mono);
      font-size: 12px;
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 10px;
    }
    .artifact-links a:hover { border-color: rgba(56, 225, 195, 0.4); }
    details { margin-top: 10px; }
    summary {
      cursor: pointer;
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 600;
      color: var(--accent);
      letter-spacing: 0.04em;
    }
    .cost-list { list-style: none; padding: 0; margin: 12px 0 0; max-width: 640px; }
    .cost-list li {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      font-family: var(--mono);
      font-size: 13px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }
    .cost-dollars { color: var(--accent); }
    .critic { border-left: 3px solid var(--accent); padding-left: 14px; margin-top: 16px; }
    .critic strong {
      font-family: var(--display);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      font-size: 13px;
      color: var(--accent);
    }
    @media (max-width: 720px) {
      header { padding: 28px 16px 0; }
      main { width: calc(100vw - 20px); }
      section { padding: 16px; }
      h1 { font-size: 26px; }
      .node-list li { grid-template-columns: 1fr; gap: 4px; }
      .timeline::before { display: none; }
      .timeline li { grid-template-columns: 34px 1fr; }
      .timeline .pill { grid-column: 2; justify-self: start; }
    }
"""


def build_html_report(session_id: str, *, gateway_url: str = "http://localhost:8109") -> str:
    parts = _assignment_report_parts(session_id, gateway_url=gateway_url)
    store = parts["store"]
    states = parts["states"]
    query = parts["query"]
    browser_out = parts["browser_out"]
    critic_out = parts["critic_out"]
    extracted = parts["extracted"]
    final_answer = parts["final_answer"]
    path = browser_out.get("path") or "(no browser node)"
    total_turns = parts["total_turns"]
    cost_summary = parts["cost_summary"]
    critic_bits = ""
    if critic_out:
        critic_bits = (
            "<div class=\"critic\">"
            f"<strong>Critic:</strong> {_html_text(critic_out.get('verdict', 'unknown'))}"
            f"<p>{_html_text(critic_out.get('rationale', ''))}</p>"
            "</div>"
        )

    status_classes = {_status_class(st.status) for st in states}
    if "bad" in status_classes:
        overall = "failed"
    elif status_classes == {"ok"}:
        overall = "complete"
    else:
        overall = "partial"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Replay Report - {_html_text(session_id)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
{_REPORT_CSS}
  </style>
</head>
<body>
  <header>
    <p class="eyebrow">Session Replay // Flight Recorder</p>
    <h1>Browser Comparison Replay Report</h1>
    <p class="session-line">Session <strong>{_html_text(session_id)}</strong></p>
    <div class="stats">
      <span class="stat"><span class="stat-label">Run</span>{_status_pill(overall)}</span>
      <span class="stat"><span class="stat-label">Nodes</span><span class="stat-value">{len(states)}</span></span>
      <span class="stat"><span class="stat-label">Browser turns</span><span class="stat-value">{_html_text(browser_out.get("turns", 0))}</span></span>
      <span class="stat"><span class="stat-label">Total turns</span><span class="stat-value">{_html_text(total_turns)}</span></span>
    </div>
    <nav class="toc">
      <a href="#goal"><span>01</span>Goal</a>
      <a href="#dag"><span>02</span>DAG</a>
      <a href="#path"><span>03</span>Path</a>
      <a href="#actions"><span>04</span>Actions</a>
      <a href="#artifacts"><span>05</span>Artifacts</a>
      <a href="#extracted"><span>06</span>Extracted</a>
      <a href="#final"><span>07</span>Comparison</a>
      <a href="#cost"><span>08</span>Cost</a>
    </nav>
  </header>
  <main>
    <section id="goal">
      <h2>1. Original User Goal</h2>
      <p>{_html_text(query or "(missing query)")}</p>
    </section>
    <section id="dag">
      <h2>2. Planner DAG</h2>
      {_html_dag(store, states)}
      <h3>Node Summary</h3>
      {_html_node_summaries(states)}
    </section>
    <section id="path">
      <h2>3. Browser Path Chosen</h2>
      <span class="badge">{_html_text(path)}</span>
    </section>
    <section id="actions">
      <h2>4. Browser Actions Taken</h2>
      {_html_actions(browser_out.get("actions", []) or [])}
    </section>
    <section id="artifacts">
      <h2>5. Screenshots Or Page-State Logs</h2>
      {_html_artifacts(browser_out)}
    </section>
    <section id="extracted">
      <h2>6. Extracted Data</h2>
      {_html_extracted(extracted)}
      {critic_bits}
    </section>
    <section id="final">
      <h2>7. Final Comparison Table</h2>
      {_render_markdown_table(final_answer or "(no formatter final_answer)")}
    </section>
    <section id="cost">
      <h2>8. Turn Count And Cost Summary</h2>
      <p><strong>Browser turns:</strong> {_html_text(browser_out.get("turns", 0))}</p>
      <p><strong>Total browser/action turns across nodes:</strong> {_html_text(total_turns)}</p>
      {_html_cost_summary(cost_summary)}
    </section>
  </main>
</body>
</html>
"""


def write_html_report(
    session_id: str,
    output_path: str | Path | None = None,
    *,
    gateway_url: str = "http://localhost:8109",
) -> Path:
    html_report = build_html_report(session_id, gateway_url=gateway_url)
    if output_path is None:
        output = SessionStore(session_id).dir / "replay_report.html"
    else:
        output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_report, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("session_id", nargs="?")
    parser.add_argument("-h", "--help", action="store_true")
    ns = parser.parse_args(sys.argv[1:])
    if ns.help:
        print("usage: uv run python replay.py [--report|--html] [--output path] <session_id>")
        return 0
    if ns.report and ns.html:
        print("replay: choose only one of --report or --html", file=sys.stderr)
        return 2
    if ns.output and not ns.html:
        print("replay: --output is only supported with --html", file=sys.stderr)
        return 2
    if not ns.session_id:
        sessions = list_sessions()
        if not sessions:
            print("replay: no sessions under state/sessions/", file=sys.stderr)
            return 2
        print("available sessions:")
        for s in sessions:
            print(f"  {s}")
        print("\nusage: uv run python replay.py [--report|--html] [--output path] <session_id>")
        return 0
    if ns.report:
        try:
            print(build_report(ns.session_id))
            return 0
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 2
    if ns.html:
        try:
            print(write_html_report(ns.session_id, ns.output))
            return 0
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 2
    return replay(ns.session_id)


if __name__ == "__main__":
    sys.exit(main())
