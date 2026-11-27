"""Single Source of Truth loader and landing HTML renderer for installation guides and client links."""

from __future__ import annotations

import functools
import html
import json
from pathlib import Path
from typing import Any

GUIDES_DATA_PATH = (Path(__file__).resolve().parents[1] / "config" / "guides_data.json").resolve()

_LANDING_ICONS = {
    "globe": '<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8"/><path d="M11.5 3a15 15 0 0 0 0 18M12.5 3a15 15 0 0 1 0 18"/></svg>',
    "user": '<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    "play": '<svg class="{cls}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3.61 1.814L13.793 12 3.61 22.186a1.05 1.05 0 0 1-.61-.959V2.773c0-.419.246-.785.61-.959zm10.89 10.893l2.302 2.302-10.937 6.333 8.635-8.635zm3.199-3.198l2.807 1.626a1 1 0 0 1 0 1.73l-2.808 1.626L15.206 12l2.493-2.491zM5.864 2.658L16.8 8.99l-2.302 2.302-8.634-8.634z"/></svg>',
    "download": '<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    "windows": '<svg class="{cls}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3 5.45L10.5 4.36V11.27H3V5.45ZM10.5 12.73V19.64L3 18.55V12.73H10.5ZM12 4.14L21 2.85V11.27H12V4.14ZM21 12.73V21.15L12 19.86V12.73H21Z"/></svg>',
    "monitor": '<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>',
    "terminal": '<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
}

_EXT_ARROW_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'
_COPY_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'


@functools.lru_cache(maxsize=1)
def get_guides_data() -> dict[str, Any]:
    """Load canonical guides and client links JSON once into memory."""
    return json.loads(GUIDES_DATA_PATH.read_text(encoding="utf-8"))


def render_landing_connect_links(connect_html: str) -> str:
    """Substitute {{URL_<LINK_KEY>}} placeholders in connect_view.html from guides_data.json."""
    links: dict[str, str] = get_guides_data().get("links", {})
    out = connect_html
    for key, url in links.items():
        out = out.replace(f"{{{{URL_{key.upper()}}}}}", html.escape(url, quote=True))
    return out


def _render_video_block(video: dict[str, Any]) -> str:
    return (
        '<details class="guide-video-details">'
        '<summary class="guide-video-summary">'
        '<span class="guide-video-play" aria-hidden="true">'
        '<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>'
        "</span>"
        '<span class="guide-video-info">'
        '<span class="guide-video-title-row">'
        f'<span class="guide-video-title">{html.escape(video["title"])}</span>'
        f'<span class="guide-video-tag">{html.escape(video["tag"])}</span>'
        "</span>"
        f'<span class="guide-video-meta">{html.escape(video["meta"])}</span>'
        "</span>"
        '<span class="disclosure-pill"><span>Смотреть</span>'
        '<svg class="guide-video-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<polyline points="6 9 12 15 18 9"/>'
        "</svg></span></summary>"
        '<div class="guide-video-wrap">'
        f'<video class="guide-video" controls playsinline preload="none" poster="{video["poster_placeholder"]}">'
        f'<source src="/sub/assets/{video["video_file"]}" type="video/mp4">'
        f'<track kind="captions" srclang="ru" label="Русские субтитры" src="{video["vtt_src"]}">'
        "</video>"
        f'<div class="guide-step-note-plain" style="margin-top:8px; font-size:12px;">{html.escape(video["caption_landing"])}</div>'
        "</div></details>"
    )


def _render_callout(callout: dict[str, Any]) -> str:
    is_warn = callout.get("tone") == "warn"
    cls = "guide-callout guide-callout-muted" if is_warn else "guide-callout"
    icon = (
        '<svg class="guide-callout-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
        if is_warn
        else '<svg class="guide-callout-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
    )
    return f'<div class="{cls}">{icon}<div>{callout["html"]}</div></div>'


def _render_kz_address(kz: dict[str, Any]) -> str:
    rows_html = []
    for row in kz["rows"]:
        copy_val = row["copy"].replace("'", "\\'")
        rows_html.append(
            '<div class="guide-data-row">'
            f'<span class="guide-data-label">{html.escape(row["label"])}</span>'
            '<div class="guide-data-val-group">'
            f'<span class="guide-data-value">{html.escape(row["display"])}</span>'
            f'<button type="button" class="mini-copy-btn" onclick="copyGuideField(\'{copy_val}\', this)" title="Скопировать" aria-label="{html.escape(row["aria"])}">'
            f'{_COPY_SVG}<span class="copy-lbl">Копировать</span></button>'
            "</div></div>"
        )
    return (
        '<div class="guide-data-card">'
        f'<div class="guide-data-card-title">{html.escape(kz["landing_title"])}</div>'
        f'{"".join(rows_html)}'
        "</div>"
    )


def _render_stores(stores: list[dict[str, Any]], links: dict[str, str]) -> str:
    cards = []
    for st in stores:
        href = html.escape(links[st["link_key"]], quote=True)
        icon = _LANDING_ICONS.get(st.get("icon", "globe"), "").format(cls="")
        tag = (
            f' <span class="guide-tf-badge">{html.escape(st["recommended_tag"])}</span>'
            if st.get("recommended_tag")
            else ""
        )
        cards.append(
            f'<a href="{href}" target="_blank" rel="noopener noreferrer" class="guide-store-card">'
            '<div class="guide-store-left">'
            f'<div class="guide-store-icon" aria-hidden="true">{icon}</div>'
            '<div class="guide-store-info">'
            f'<span class="guide-store-name">{html.escape(st["name"])}{tag}</span>'
            f'<span class="guide-store-sub">{html.escape(st["sub"])}</span>'
            "</div></div>"
            f'<span class="guide-store-badge">{html.escape(st["badge"])} {_EXT_ARROW_SVG}</span>'
            "</a>"
        )
    return f'<div class="guide-store-grid guide-store-grid-single">{"".join(cards)}</div>'


def _render_shot(shot: dict[str, Any]) -> str:
    orient = shot.get("orientation", "vertical")
    return (
        f'<div class="guide-screenshot-wrapper {orient}" role="button" tabindex="0" aria-label="{html.escape(shot["aria"])}">'
        f'<img src="{shot["placeholder"]}" alt="{html.escape(shot["alt"])}" class="guide-real-screenshot loaded" decoding="async" onload="markImgLoaded(this)">'
        "</div>"
    )


def _render_step_card(step: dict[str, Any], data: dict[str, Any]) -> str:
    links: dict[str, str] = data["links"]
    num_id = f' id="{step["landing_num_id"]}"' if step.get("landing_num_id") else ""
    body_html = step.get("html_landing") or step.get("html", "")
    parts = [
        '<div class="guide-step-card">',
        '<div class="guide-step-header">'
        f'<span class="guide-step-num"{num_id}>{step["num"]}</span>'
        f'<span class="guide-step-title">{html.escape(step["title"])}</span>'
        "</div>",
        f'<div class="guide-step-desc">{body_html}</div>',
    ]
    if step.get("stores"):
        parts.append(_render_stores(step["stores"], links))
    if step.get("tip_desc_landing"):
        parts.append(f'<div class="guide-tip-desc" style="margin-top: 12px;">{step["tip_desc_landing"]}</div>')
    if step.get("has_kz_address"):
        parts.append(_render_kz_address(data["kz_billing_address"]))
    if step.get("tip"):
        tip = step["tip"]
        tip_inner = [
            '<div class="guide-tip-box">',
            f'<div class="guide-tip-title"><span>{html.escape(tip["title"])}</span></div>',
            f'<div class="guide-tip-desc">{tip["html"]}</div>',
        ]
        if step.get("shot"):
            tip_inner.append(_render_shot(step["shot"]))
        if tip.get("link_key"):
            tip_href = html.escape(links[tip["link_key"]], quote=True)
            tip_inner.append(
                f'<div class="guide-tip-link-wrap"><a href="{tip_href}" target="_blank" rel="noopener noreferrer" class="guide-sub-link">'
                f'<span>{html.escape(tip["link_label"])}</span>{_EXT_ARROW_SVG}</a></div>'
            )
        tip_inner.append("</div>")
        parts.append("".join(tip_inner))
    elif step.get("shot"):
        parts.append(_render_shot(step["shot"]))

    note_text = step.get("note_landing") or step.get("note")
    if note_text:
        parts.append(f'<div class="guide-step-note-plain">{note_text}</div>')
    parts.append("</div>")
    return "".join(parts)


def _render_platform_section(
    section_id: str,
    nav_btn_id: str,
    switch_fn: str,
    tabs_aria: str,
    section_data: dict[str, Any],
    data: dict[str, Any],
    *,
    visible: bool = False,
) -> str:
    tabs_cls = "guide-method-tabs guide-method-tabs-2" if len(section_data["methods"]) == 2 else "guide-method-tabs"
    method_btns = []
    method_blocks = []
    for idx, m in enumerate(section_data["methods"]):
        is_first = idx == 0
        icon_svg = _LANDING_ICONS.get(m.get("icon", "globe"), "").format(cls="guide-tab-icon")
        method_btns.append(
            f'<button type="button" class="guide-method-btn{" active" if is_first else ""}" '
            f'id="{m["landing_btn_id"]}" onclick="{switch_fn}(\'{m["id"]}\')" '
            f'role="tab" aria-selected="{"true" if is_first else "false"}" tabindex="{"0" if is_first else "-1"}">'
            f'<span class="guide-method-badge">{html.escape(m["badge"])}</span>'
            f'<span class="guide-method-title">{icon_svg}<span>{html.escape(m["title"])}</span></span>'
            f'<span class="guide-method-sub">{html.escape(m["sub"])}</span>'
            "</button>"
        )
        block_parts = [f'<div id="{m["landing_sub_id"]}"{"" if is_first else " style=\"display: none;\""}>']
        if m.get("has_video"):
            block_parts.append(_render_video_block(data["video"]))
        for c in m.get("callouts", []):
            block_parts.append(_render_callout(c))
        for st in m.get("steps", []):
            block_parts.append(_render_step_card(st, data))
        block_parts.append("</div>")
        method_blocks.append("".join(block_parts))

    common_html = "".join(_render_step_card(st, data) for st in section_data.get("common_steps", []))
    style_attr = "" if visible else ' style="display: none;"'
    return (
        f'<div id="{section_id}" class="guide-section" role="tabpanel" aria-labelledby="{nav_btn_id}"{style_attr}>'
        f'<h2 class="guide-step-prompt">{html.escape(section_data["prompt"])}</h2>'
        f'<div class="{tabs_cls}" role="tablist" aria-label="{html.escape(tabs_aria)}">{"".join(method_btns)}</div>'
        f'{"".join(method_blocks)}'
        f"{common_html}"
        "</div>"
    )


def _render_faq_section(faq_items: list[dict[str, str]]) -> str:
    blocks = []
    for idx, item in enumerate(faq_items):
        is_open = idx == 0
        blocks.append(
            f'<div class="guide-faq-item{" open" if is_open else ""}">'
            f'<button type="button" class="guide-faq-q" onclick="toggleGuideFaq(this)" aria-expanded="{"true" if is_open else "false"}">'
            f'<span>{html.escape(item["q"])}</span>'
            '<span class="disclosure-pill"><span>Ответ</span>'
            '<svg class="guide-faq-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="6 9 12 15 18 9"/></svg>'
            "</span></button>"
            f'<div class="guide-faq-a">{item["a_html"]}</div>'
            "</div>"
        )
    return f'<div id="guideSectionFaq" class="guide-section" role="tabpanel" aria-labelledby="guideNavBtnFaq" style="display: none;">{"".join(blocks)}</div>'


def render_landing_guide_sections() -> dict[str, str]:
    """Render all 4 landing guide sections from config/guides_data.json."""
    data = get_guides_data()
    return {
        "ios": _render_platform_section(
            "guideSectionIos",
            "guideNavBtnIos",
            "switchIosMethod",
            "Способ установки iOS",
            data["ios"],
            data,
            visible=True,
        ),
        "android": _render_platform_section(
            "guideSectionAndroid",
            "guideNavBtnAndroid",
            "switchAndroidMethod",
            "Способ установки Android",
            data["android"],
            data,
        ),
        "desktop": _render_platform_section(
            "guideSectionDesktop",
            "guideNavBtnDesktop",
            "switchDesktopMethod",
            "Операционная система",
            data["desktop"],
            data,
        ),
        "faq": _render_faq_section(data["faq"]),
    }
