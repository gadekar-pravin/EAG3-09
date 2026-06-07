from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replay
from browser.recipes import (
    build_hf_extracted_data,
    extract_hf_model_cards,
    format_hf_cards_content,
)
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


def test_huggingface_extraction_ignores_distractors_and_sorts_likes() -> None:
    html = """
    <body>
      <nav>
        <a href="/docs/transformers">Transformers docs</a>
        <a href="/blog/text-generation">Text generation blog</a>
        <a href="/datasets/squad">SQuAD dataset</a>
        <a href="/spaces/demo/text-generation">Demo Space</a>
        <a href="/models?sort=likes">Models listing</a>
        <a href="/real/NoCard">12.4k likes outside a card</a>
      </nav>
      <main>
        <article class="model-card">
          <a href="/beta/Model-B">beta/Model-B</a>
          <p>Text Generation model for fixtures</p>
          <span>4k likes</span><span>11k downloads</span>
        </article>
        <article class="model-card">
          <a href="/alpha/Model-A">alpha/Model-A</a>
          <p>Instruction tuned text-generation model</p>
          <span>12.4k likes</span><span>1.2M downloads</span>
        </article>
        <article class="model-card">
          <a href="/gamma/Model-C">gamma/Model-C</a>
          <p>Text Generation model with hidden popularity labels</p>
        </article>
      </main>
    </body>
    """

    cards = extract_hf_model_cards(html)
    content = format_hf_cards_content(cards)

    assert len(cards) == 3
    assert [c["model_id"] for c in cards] == [
        "alpha/Model-A",
        "beta/Model-B",
        "gamma/Model-C",
    ]
    assert [c["rank"] for c in cards] == [1, 2, 3]
    assert all(not c["model_id"].startswith(("docs/", "blog/", "datasets/", "spaces/")) for c in cards)
    assert cards[2]["likes"] == ""
    assert cards[2]["downloads"] == ""
    assert "likes=unavailable" in content


def test_huggingface_extraction_reports_sort_verification_metadata() -> None:
    complete_cards = [
        {"model_id": "alpha/Model-A", "likes": "12.4k"},
        {"model_id": "beta/Model-B", "likes": "4k"},
        {"model_id": "gamma/Model-C", "likes": "900"},
    ]
    cards = [
        {"model_id": "alpha/Model-A", "likes": "12.4k"},
        {"model_id": "beta/Model-B", "likes": "4k"},
        {"model_id": "gamma/Model-C", "likes": ""},
    ]

    data = build_hf_extracted_data(
        complete_cards,
        "https://huggingface.co/models?pipeline_tag=text-generation&sort=likes",
    )
    assert data["sort_verified"] is True
    assert data["warnings"] == []

    data = build_hf_extracted_data(
        cards,
        "https://huggingface.co/models?pipeline_tag=text-generation&sort=likes",
    )
    assert data["sort_verified"] is False
    assert data["warnings"] == ["one or more model cards did not expose a parseable likes count"]

    data = build_hf_extracted_data(cards, "https://huggingface.co/models")
    assert data["sort_verified"] is False
    assert "final URL does not expose sort=likes after selecting Most Likes" in data["warnings"]


def test_huggingface_extraction_parses_current_rendered_metric_layout() -> None:
    html = """
    <main>
      <article class="model-card">
        <a href="/deepseek-ai/DeepSeek-R1">deepseek-ai/DeepSeek-R1</a>
        <span>Text Generation</span>
        <span>•</span><span>685B</span>
        <span>•</span><span>Updated Mar 27, 2025</span>
        <span>•</span><span>5.75M</span>
        <span>•</span><span>•</span><span>13.4k</span>
      </article>
      <article class="model-card">
        <a href="/meta-llama/Meta-Llama-3-8B">meta-llama/Meta-Llama-3-8B</a>
        <span>Text Generation</span>
        <span>•</span><span>8B</span>
        <span>•</span><span>Updated Sep 27, 2024</span>
        <span>•</span><span>1.52M</span>
        <span>•</span><span>•</span><span>6.57k</span>
      </article>
      <article class="model-card">
        <a href="/meta-llama/Llama-3.1-8B-Instruct">meta-llama/Llama-3.1-8B-Instruct</a>
        <span>Text Generation</span>
        <span>•</span><span>8B</span>
        <span>•</span><span>Updated Sep 25, 2024</span>
        <span>•</span><span>11.3M</span>
        <span>•</span><span>•</span><span>6.01k</span>
      </article>
    </main>
    """

    cards = extract_hf_model_cards(html)
    data = build_hf_extracted_data(
        cards,
        "https://huggingface.co/models?pipeline_tag=text-generation&sort=likes",
    )

    assert [c["model_id"] for c in cards] == [
        "deepseek-ai/DeepSeek-R1",
        "meta-llama/Meta-Llama-3-8B",
        "meta-llama/Llama-3.1-8B-Instruct",
    ]
    assert [c["likes"] for c in cards] == ["13.4k", "6.57k", "6.01k"]
    assert [c["downloads"] for c in cards] == ["5.75M", "1.52M", "11.3M"]
    assert [c["parameters"] for c in cards] == ["685B", "8B", "8B"]
    assert [c["description"] for c in cards] == ["", "", ""]
    assert data["sort_verified"] is True
    assert data["warnings"] == []


class _FakeStore:
    page_state_logs: list[str] = ["/tmp/browser/01.txt"]
    query = "Compare top 3 Hugging Face text-generation models sorted by likes."
    model_id = "alpha/Model-A"
    final_answer = "| Rank | Model |\n|---|---|\n| 1 | alpha/Model-A |"
    dir = Path("/tmp/browser-session")

    def __init__(self, _session_id: str):
        return None

    def read_query(self) -> str:
        return self.query

    def read_graph(self):
        g = nx.DiGraph()
        for nid, skill in [
            ("n:1", "planner"),
            ("n:2", "browser"),
            ("n:3", "distiller"),
            ("n:5", "critic"),
            ("n:4", "formatter"),
        ]:
            g.add_node(nid, skill=skill, status="complete")
        g.add_edges_from([("n:1", "n:2"), ("n:2", "n:3"), ("n:3", "n:5"), ("n:5", "n:4")])
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
            "page_state_logs": self.page_state_logs,
            "extracted_data": {"models": [{"rank": 1, "model_id": self.model_id}]},
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
                node_id="n:5",
                skill="critic",
                status="complete",
                result=AgentResult(
                    success=True,
                    agent_name="critic",
                    output={"verdict": "pass", "rationale": "The extracted rows are supported."},
                ),
            ),
            NodeState(
                node_id="n:4",
                skill="formatter",
                status="complete",
                result=AgentResult(
                    success=True,
                    agent_name="formatter",
                    output={"final_answer": self.final_answer},
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


def test_replay_html_report_renders_assignment_sections(monkeypatch, tmp_path) -> None:
    png = tmp_path / "01_text_generation.png"
    txt = tmp_path / "01_text_generation.txt"
    png.write_bytes(b"not-really-a-png")
    txt.write_text("page state with <unsafe> text", encoding="utf-8")

    class FakeHtmlStore(_FakeStore):
        page_state_logs = [str(png), str(txt)]
        query = "Compare <top 3> Hugging Face models."
        model_id = "alpha/<Model-A>"
        final_answer = (
            "Here is the table:\n\n"
            "| Rank | Model | URL |\n"
            "| :--- | :--- | :--- |\n"
            "| 1 | alpha/<Model-A> | [Link](https://huggingface.co/alpha/Model-A) |"
        )

    monkeypatch.setattr(replay, "SessionStore", FakeHtmlStore)
    monkeypatch.setattr(replay, "_fetch_cost_summary", lambda *_args, **_kwargs: "- browser/gemini: calls=3")

    html = replay.build_html_report("sid")

    for heading in [
        "1. Original User Goal",
        "2. Planner DAG",
        "3. Browser Path Chosen",
        "4. Browser Actions Taken",
        "5. Screenshots Or Page-State Logs",
        "6. Extracted Data",
        "7. Final Comparison Table",
        "8. Turn Count And Cost Summary",
    ]:
        assert heading in html
    assert '<span class="badge">deterministic</span>' in html
    assert "Text Generation" in html
    assert "Critic:" in html
    assert "pass" in html
    assert "browser/gemini: calls=3" in html
    assert "<img" in html
    assert "01_text_generation.png" in html
    assert "<details><summary>Page-state log</summary>" in html
    assert "page state with &lt;unsafe&gt; text" in html
    assert "<table>" in html
    assert "&lt;top 3&gt;" in html
    assert "alpha/&lt;Model-A&gt;" in html
    assert "<script" not in html


def test_replay_html_cli_writes_output_path(monkeypatch, tmp_path) -> None:
    out = tmp_path / "report.html"

    monkeypatch.setattr(replay, "SessionStore", _FakeStore)
    monkeypatch.setattr(replay, "_fetch_cost_summary", lambda *_args, **_kwargs: "- browser/gemini: calls=3")
    monkeypatch.setattr(sys, "argv", ["replay.py", "--html", "--output", str(out), "sid"])

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert replay.main() == 0

    assert stdout.getvalue().strip() == str(out)
    text = out.read_text(encoding="utf-8")
    assert "Browser Comparison Replay Report" in text
    assert "alpha/Model-A" in text


def test_planner_prompt_guards_huggingface_assignment_route() -> None:
    prompt = Path("prompts/planner.md").read_text()

    assert "Assignment-critical route" in prompt
    assert "https://huggingface.co/models" in prompt
    assert "Do not answer this assignment from MEMORY HITS" in prompt
    assert "Browser → Distiller → Formatter" in prompt


def test_formatter_prompt_includes_huggingface_parameters_column() -> None:
    prompt = Path("prompts/formatter.md").read_text()

    assert "Rank, Model, Likes, Downloads, Parameters, Description, URL" in prompt
