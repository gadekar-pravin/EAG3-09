from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replay
from browser.recipes import extract_hf_model_cards, format_hf_cards_content
from browser.skill import BrowserSkill
from schemas import AgentResult, NodeState


def test_browser_blocked_pack_uses_blocked_path() -> None:
    skill = BrowserSkill()
    result = skill._pack_error(  # noqa: SLF001 - pins assignment-facing contract
        "https://example.com", "goal", "gateway_blocked", "captcha"
    )

    assert result.success is False
    assert result.error_code == "gateway_blocked"
    assert result.output["path"] == "blocked"


def test_huggingface_card_extraction_from_rendered_fixture() -> None:
    html = """
    <main>
      <article class="model-card">
        <a href="/alpha/Model-A">alpha/Model-A</a>
        <p>Small text-generation model</p>
        <span>12.4k likes</span><span>1.2M downloads</span>
      </article>
      <article class="model-card">
        <a href="/beta/Model-B">beta/Model-B</a>
        <p>Instruction-tuned model</p>
        <span>8,888 downloads</span><span>9.1k likes</span>
      </article>
      <article class="model-card">
        <a href="/gamma/Model-C">gamma/Model-C</a>
        <p>Compact chat model</p>
        <span>7k likes</span>
      </article>
      <article class="model-card">
        <a href="/delta/Model-D">delta/Model-D</a>
        <p>Should not appear</p>
        <span>1 like</span>
      </article>
    </main>
    """

    cards = extract_hf_model_cards(html)
    content = format_hf_cards_content(cards)

    assert len(cards) == 3
    assert [c["model_id"] for c in cards] == [
        "alpha/Model-A",
        "beta/Model-B",
        "gamma/Model-C",
    ]
    assert "EXTRACTED_MODEL_CARDS_JSON" in content
    assert cards[0]["likes"] == "12.4k"
    assert cards[1]["downloads"] == "8,888"


class _FakeStore:
    def __init__(self, _session_id: str):
        return None

    def read_query(self) -> str:
        return "Compare top 3 Hugging Face text-generation models sorted by likes."

    def read_graph(self):
        g = nx.DiGraph()
        for nid, skill in [
            ("n:1", "planner"),
            ("n:2", "browser"),
            ("n:3", "distiller"),
            ("n:4", "formatter"),
        ]:
            g.add_node(nid, skill=skill, status="complete")
        g.add_edges_from([("n:1", "n:2"), ("n:2", "n:3"), ("n:3", "n:4")])
        return g

    def read_all_nodes(self) -> list[NodeState]:
        browser_output = {
            "url": "https://huggingface.co/models",
            "goal": "filter Text Generation and sort by Most Likes",
            "path": "deterministic",
            "turns": 3,
            "actions": [
                {"turn": 1, "actions": [{"type": "click", "label": "Text Generation"}], "outcome": "ok"},
                {"turn": 2, "actions": [{"type": "click", "label": "Sort menu"}], "outcome": "ok"},
                {"turn": 3, "actions": [{"type": "click", "label": "Most Likes"}], "outcome": "ok"},
            ],
            "final_url": "https://huggingface.co/models?pipeline_tag=text-generation&sort=likes",
            "artifacts_dir": "/tmp/browser",
            "page_state_logs": ["/tmp/browser/01.txt"],
            "extracted_data": {"models": [{"rank": 1, "model_id": "alpha/Model-A"}]},
        }
        return [
            NodeState(node_id="n:1", skill="planner", status="complete"),
            NodeState(
                node_id="n:2",
                skill="browser",
                status="complete",
                result=AgentResult(success=True, agent_name="browser", output=browser_output),
            ),
            NodeState(
                node_id="n:3",
                skill="distiller",
                status="complete",
                result=AgentResult(success=True, agent_name="distiller", output={"fields": {}}),
            ),
            NodeState(
                node_id="n:4",
                skill="formatter",
                status="complete",
                result=AgentResult(
                    success=True,
                    agent_name="formatter",
                    output={"final_answer": "| Rank | Model |\n|---|---|\n| 1 | alpha/Model-A |"},
                ),
            ),
        ]


def test_replay_report_renders_assignment_sections(monkeypatch) -> None:
    monkeypatch.setattr(replay, "SessionStore", _FakeStore)
    monkeypatch.setattr(replay, "_fetch_cost_summary", lambda *_args, **_kwargs: "- browser/gemini: calls=3")

    report = replay.build_report("sid")

    for heading in [
        "## 1. Original User Goal",
        "## 2. Planner DAG",
        "## 3. Browser Path Chosen",
        "## 4. Browser Actions Taken",
        "## 5. Screenshots Or Page-State Logs",
        "## 6. Extracted Data",
        "## 7. Final Comparison Table",
        "## 8. Turn Count And Cost Summary",
    ]:
        assert heading in report
    assert "deterministic" in report
    assert "Text Generation" in report
    assert "alpha/Model-A" in report
    assert "browser/gemini" in report


def test_planner_prompt_guards_huggingface_assignment_route() -> None:
    prompt = Path("prompts/planner.md").read_text()

    assert "Assignment-critical route" in prompt
    assert "https://huggingface.co/models" in prompt
    assert "Do not answer this assignment from MEMORY HITS" in prompt
    assert "Browser → Distiller → Formatter" in prompt
