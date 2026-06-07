"""Deterministic browser recipes for known assignment targets.

The generic Browser cascade remains the default. Recipes live here only for
sites where the assignment explicitly wants visible, deterministic browser
actions before extraction.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html


@dataclass
class RecipeResult:
    content: str
    actions: list[dict]
    final_url: str
    extracted_data: dict[str, Any]
    page_state_logs: list[str] = field(default_factory=list)
    artifacts_dir: str | None = None


_HF_HOST_RE = re.compile(r"^https?://(?:www\.)?huggingface\.co/models/?", re.I)
_MODEL_PATH_RE = re.compile(r"^/([^/\s?#]+/[^/\s?#]+)")
_COUNT = r"\d[\d,]*(?:\.\d+)?\s*[kKmM]?"
_LIKES_RE = re.compile(rf"(?P<num>{_COUNT})\s*(?:likes?|like)", re.I)
_DOWNLOADS_RE = re.compile(rf"(?P<num>{_COUNT})\s*(?:downloads?|download)", re.I)


def is_huggingface_model_comparison(url: str, goal: str) -> bool:
    g = goal.lower()
    return (
        bool(_HF_HOST_RE.match(url or ""))
        and "text" in g
        and "generation" in g
        and "like" in g
        and ("top 3" in g or "three" in g or "3 " in g)
    )


def _text(node) -> str:
    return " ".join(t.strip() for t in node.itertext() if t and t.strip())


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    return (m.group("num").strip() if m else "")


def extract_hf_model_cards(html_text: str, *, base_url: str = "https://huggingface.co") -> list[dict]:
    """Extract top rendered HF model cards from HTML.

    The helper intentionally parses rendered HTML, not search snippets. It
    accepts the real Hugging Face card shape and small test fixtures with the
    same model-link contract.
    """
    root = lxml_html.fromstring(html_text or "<html></html>")
    seen: set[str] = set()
    cards: list[dict] = []
    links = root.xpath('//a[starts-with(@href, "/") or starts-with(@href, "https://huggingface.co/")]')
    for link in links:
        href = link.get("href") or ""
        if href.startswith("https://huggingface.co/"):
            path = href.replace("https://huggingface.co", "", 1)
        else:
            path = href
        m = _MODEL_PATH_RE.match(path)
        if not m:
            continue
        model_id = m.group(1).strip("/")
        if model_id in seen:
            continue
        seen.add(model_id)

        container = link
        for ancestor in link.iterancestors():
            cls = " ".join(ancestor.get("class", "").split()).lower()
            role = (ancestor.get("role") or "").lower()
            tag = ancestor.tag.lower() if isinstance(ancestor.tag, str) else ""
            if tag in {"article", "li"} or "model" in cls or "card" in cls or role == "listitem":
                container = ancestor
                break
        text = _text(container)
        if len(text) < len(model_id):
            text = _text(link)
        lines = [ln.strip() for ln in re.split(r"\s{2,}|\n", text) if ln.strip()]
        description = ""
        for ln in lines:
            if model_id not in ln and not _LIKES_RE.search(ln) and not _DOWNLOADS_RE.search(ln):
                description = ln
                break
        cards.append({
            "rank": len(cards) + 1,
            "model_id": model_id,
            "model_url": urljoin(base_url, "/" + model_id),
            "likes": _first_match(_LIKES_RE, text),
            "downloads": _first_match(_DOWNLOADS_RE, text),
            "description": description,
        })
        if len(cards) == 3:
            break
    return cards


def format_hf_cards_content(cards: list[dict]) -> str:
    rows = json.dumps(cards, indent=2, ensure_ascii=False)
    lines = [
        "EXTRACTED_MODEL_CARDS_JSON:",
        rows,
        "",
        "Rendered top-three Hugging Face text-generation model cards sorted by likes.",
    ]
    for card in cards:
        lines.append(
            f"{card.get('rank')}. {card.get('model_id')} | "
            f"likes={card.get('likes') or 'unavailable'} | "
            f"downloads={card.get('downloads') or 'unavailable'} | "
            f"description={card.get('description') or 'unavailable'} | "
            f"url={card.get('model_url')}"
        )
    return "\n".join(lines)


async def _click_first(page, selectors: list[str], label: str, actions: list[dict], turn: int) -> bool:
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            await loc.wait_for(state="visible", timeout=5000)
            await loc.click()
            actions.append({
                "turn": turn,
                "actions": [{"type": "click", "selector": selector, "label": label}],
                "outcome": "ok",
            })
            return True
        except Exception:  # noqa: BLE001 - selector fallback
            continue
    actions.append({
        "turn": turn,
        "actions": [{"type": "click", "label": label}],
        "outcome": "not found",
    })
    return False


async def run_huggingface_top_models_recipe(page, *, url: str, artifacts_dir: str | None = None) -> RecipeResult:
    """Drive the HF model listing through visible controls and extract cards."""
    actions: list[dict] = []
    logs: list[str] = []
    art_dir = Path(artifacts_dir) if artifacts_dir else None
    if art_dir:
        art_dir.mkdir(parents=True, exist_ok=True)

    async def record_state(name: str) -> None:
        if not art_dir:
            return
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
        png = art_dir / f"{safe}.png"
        txt = art_dir / f"{safe}.txt"
        await page.screenshot(path=str(png), full_page=False)
        txt.write_text(
            f"url: {page.url}\n"
            f"title: {await page.title()}\n"
            f"body_preview: {(await page.locator('body').inner_text(timeout=5000))[:4000]}\n",
            encoding="utf-8",
        )
        logs.extend([str(png), str(txt)])

    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1000)
    await record_state("00_loaded")

    task_clicked = await _click_first(page, [
        'a[href*="pipeline_tag=text-generation"]',
        'button:has-text("Text Generation")',
        'a:has-text("Text Generation")',
        'label:has-text("Text Generation")',
    ], "Text Generation", actions, 1)
    if not task_clicked:
        return RecipeResult("", actions, page.url, {"models": []}, logs, str(art_dir) if art_dir else None)
    await page.wait_for_load_state("domcontentloaded", timeout=15000)
    await page.wait_for_timeout(1000)
    await record_state("01_text_generation")

    sort_opened = await _click_first(page, [
        'button:has-text("Sort")',
        'summary:has-text("Sort")',
        '[role="button"]:has-text("Sort")',
        'button:has-text("Trending")',
        'button:has-text("Recently Updated")',
    ], "Sort menu", actions, 2)
    if not sort_opened:
        return RecipeResult("", actions, page.url, {"models": []}, logs, str(art_dir) if art_dir else None)
    await page.wait_for_timeout(700)
    await record_state("02_sort_open")

    likes_clicked = await _click_first(page, [
        'a[href*="sort=likes"]',
        'button:has-text("Most Likes")',
        'a:has-text("Most Likes")',
        '[role="menuitem"]:has-text("Most Likes")',
        'button:has-text("Likes")',
        'a:has-text("Likes")',
    ], "Most Likes", actions, 3)
    if not likes_clicked:
        return RecipeResult("", actions, page.url, {"models": []}, logs, str(art_dir) if art_dir else None)
    await page.wait_for_load_state("domcontentloaded", timeout=15000)
    await page.wait_for_timeout(1500)
    await record_state("03_most_likes")

    rendered = await page.content()
    cards = extract_hf_model_cards(rendered)
    if len(cards) < 3:
        return RecipeResult("", actions, page.url, {"models": cards}, logs, str(art_dir) if art_dir else None)

    return RecipeResult(
        content=format_hf_cards_content(cards),
        actions=actions,
        final_url=page.url,
        extracted_data={"models": cards},
        page_state_logs=logs,
        artifacts_dir=str(art_dir) if art_dir else None,
    )
