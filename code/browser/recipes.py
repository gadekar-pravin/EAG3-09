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
from urllib.parse import urljoin, urlparse

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
_MODEL_SIGNAL_RE = re.compile(
    r"\b(?:text-generation|text generation|transformers|safetensors|updated|"
    r"downloads?|likes?|model card)\b",
    re.I,
)
_NON_MODEL_PREFIXES = {
    "-",
    "api",
    "blog",
    "chat",
    "collections",
    "datasets",
    "docs",
    "enterprise",
    "events",
    "join",
    "login",
    "models",
    "new",
    "organizations",
    "papers",
    "pricing",
    "settings",
    "spaces",
    "tasks",
}


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


def _parse_count(value: str) -> float | None:
    raw = (value or "").strip().lower().replace(",", "")
    if not raw:
        return None
    multiplier = 1.0
    if raw.endswith("k"):
        multiplier = 1_000.0
        raw = raw[:-1]
    elif raw.endswith("m"):
        multiplier = 1_000_000.0
        raw = raw[:-1]
    try:
        return float(raw.strip()) * multiplier
    except ValueError:
        return None


def _is_probable_model_path(path: str) -> bool:
    parsed = urlparse(path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) != 2:
        return False
    owner, name = parts
    if owner.lower() in _NON_MODEL_PREFIXES:
        return False
    if name.lower() in _NON_MODEL_PREFIXES:
        return False
    if "." in owner or owner.startswith((".", "_")):
        return False
    return True


def _card_container_for(link):
    for ancestor in link.iterancestors():
        cls = " ".join(ancestor.get("class", "").split()).lower()
        role = (ancestor.get("role") or "").lower()
        tag = ancestor.tag.lower() if isinstance(ancestor.tag, str) else ""
        if tag in {"article", "li"} or "model" in cls or "card" in cls or role == "listitem":
            return ancestor
    return None


def _has_model_card_signal(text: str) -> bool:
    return bool(_LIKES_RE.search(text) or _DOWNLOADS_RE.search(text) or _MODEL_SIGNAL_RE.search(text))


def extract_hf_model_cards(html_text: str, *, base_url: str = "https://huggingface.co") -> list[dict]:
    """Extract top rendered HF model cards from HTML.

    The helper intentionally parses rendered HTML, not search snippets. It
    accepts plausible model cards only: two-segment model links inside a card
    container with model-listing signals nearby.
    """
    root = lxml_html.fromstring(html_text or "<html></html>")
    seen: set[str] = set()
    candidates: list[tuple[float, int, dict]] = []
    links = root.xpath('//a[starts-with(@href, "/") or starts-with(@href, "https://huggingface.co/")]')
    for order, link in enumerate(links):
        href = link.get("href") or ""
        if href.startswith("https://huggingface.co/"):
            path = href.replace("https://huggingface.co", "", 1)
        else:
            path = href
        m = _MODEL_PATH_RE.match(path)
        if not m or not _is_probable_model_path(path):
            continue
        model_id = m.group(1).strip("/")
        if model_id in seen:
            continue

        container = _card_container_for(link)
        if container is None:
            continue
        text = _text(container)
        if not _has_model_card_signal(text):
            continue
        seen.add(model_id)

        likes = _first_match(_LIKES_RE, text)
        downloads = _first_match(_DOWNLOADS_RE, text)
        lines = [ln.strip() for ln in re.split(r"\s{2,}|\n", text) if ln.strip()]
        description = ""
        for ln in lines:
            if model_id not in ln and not _LIKES_RE.search(ln) and not _DOWNLOADS_RE.search(ln):
                description = ln
                break
        card = {
            "rank": 0,
            "model_id": model_id,
            "model_url": urljoin(base_url, "/" + model_id),
            "likes": likes,
            "downloads": downloads,
            "description": description,
        }
        likes_count = _parse_count(likes)
        sort_key = likes_count if likes_count is not None else -1.0
        candidates.append((sort_key, order, card))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    cards = [card for _sort_key, _order, card in candidates[:3]]
    for rank, card in enumerate(cards, start=1):
        card["rank"] = rank
    return cards


def build_hf_extracted_data(cards: list[dict], final_url: str) -> dict[str, Any]:
    likes_counts = [_parse_count(card.get("likes", "")) for card in cards]
    known_likes = [count for count in likes_counts if count is not None]
    all_likes_parseable = bool(cards) and len(known_likes) == len(cards)
    descending_likes = all(
        known_likes[i] >= known_likes[i + 1]
        for i in range(len(known_likes) - 1)
    )
    sort_verified = (
        "sort=likes" in (final_url or "").lower()
        and all_likes_parseable
        and descending_likes
    )
    warnings = []
    if "sort=likes" not in (final_url or "").lower():
        warnings.append("final URL does not expose sort=likes after selecting Most Likes")
    if len(known_likes) < len(cards):
        warnings.append("one or more model cards did not expose a parseable likes count")
    if not descending_likes:
        warnings.append("parseable likes counts were not in descending order")
    return {
        "models": cards,
        "sort_verified": sort_verified,
        "warnings": warnings,
    }


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
    extracted_data = build_hf_extracted_data(cards, page.url)
    if len(cards) < 3:
        return RecipeResult("", actions, page.url, extracted_data, logs, str(art_dir) if art_dir else None)

    return RecipeResult(
        content=format_hf_cards_content(cards),
        actions=actions,
        final_url=page.url,
        extracted_data=extracted_data,
        page_state_logs=logs,
        artifacts_dir=str(art_dir) if art_dir else None,
    )
