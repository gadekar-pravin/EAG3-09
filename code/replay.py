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
import json
import sys
from pathlib import Path

import httpx

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


def build_report(session_id: str, *, gateway_url: str = "http://localhost:8109") -> str:
    store = SessionStore(session_id)
    states = store.read_all_nodes()
    if not states:
        raise RuntimeError(f"replay: no nodes under state/sessions/{session_id}/")

    query = store.read_query() or ""
    browser = next((st for st in states if st.skill == "browser"), None)
    distiller = next((st for st in states if st.skill == "distiller"), None)
    formatter = next((st for st in reversed(states) if st.skill == "formatter"), None)

    browser_out = browser.result.output if browser and browser.result else {}
    final_answer = ""
    if formatter and formatter.result:
        final_answer = formatter.result.output.get("final_answer", "")
    extracted = browser_out.get("extracted_data") or {}
    if not extracted and distiller and distiller.result:
        extracted = distiller.result.output.get("fields", {}) or distiller.result.output

    artifact_lines = []
    if browser_out.get("artifacts_dir"):
        artifact_lines.append(f"- artifacts_dir: {browser_out['artifacts_dir']}")
    for path in browser_out.get("page_state_logs", []) or []:
        artifact_lines.append(f"- {path}")
    if not artifact_lines:
        artifact_lines.append("(none recorded)")

    total_turns = sum(
        int((st.result.output or {}).get("turns") or 0)
        for st in states
        if st.result and isinstance(st.result.output, dict)
    )

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
        f"Total browser/action turns across nodes: {total_turns}",
        _fetch_cost_summary(session_id, gateway_url=gateway_url),
    ])


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("session_id", nargs="?")
    parser.add_argument("-h", "--help", action="store_true")
    ns = parser.parse_args(sys.argv[1:])
    if ns.help:
        print("usage: uv run python replay.py [--report] <session_id>")
        return 0
    if not ns.session_id:
        sessions = list_sessions()
        if not sessions:
            print("replay: no sessions under state/sessions/", file=sys.stderr)
            return 2
        print("available sessions:")
        for s in sessions:
            print(f"  {s}")
        print("\nusage: uv run python replay.py [--report] <session_id>")
        return 0
    if ns.report:
        try:
            print(build_report(ns.session_id))
            return 0
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 2
    return replay(ns.session_id)


if __name__ == "__main__":
    sys.exit(main())
