"""STAGE 3-7 glue: build QuestionDrafts per parse batch, then materialise final QuestionOut.

build_drafts : segment -> Extract+Ground -> option layout -> visual routing   (no files written)
materialize  : assign qids, crop assets from the canonical images, build JSON models
Splitting the two lets the retry ladder re-draft a page without leaving orphan assets.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..models import OptionOut, QuestionOut
from ..utils.geometry import Box
from .crop import save_crop
from .options import RegionLayout, layout_region
from .parse_stage import BatchResult
from .segment import Region, segment_page
from .semantics import run_semantics
from .structure import mark_watermarks
from .visuals import ImageCache, VisualRef, option_visual, question_visuals

log = logging.getLogger("pipeline.assemble")
LABELS = ["A", "B", "C", "D"]


@dataclass
class OptionDraft:
    label: str
    text: str = ""
    ref: Optional[VisualRef] = None
    image_only: bool = False
    box: Optional[Box] = None


@dataclass
class QuestionDraft:
    region: Region
    layout: RegionLayout
    page: int
    column: int
    number: int
    number_text: str
    stem_text: str = ""
    correct_option: str = ""
    answer_text: str = ""
    options: dict[str, OptionDraft] = field(default_factory=dict)
    q_visuals: list[VisualRef] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    ocr_conf: Optional[float] = None
    profile: str = "default"


def _opt_text(item: Optional[dict], label: str) -> str:
    if not item:
        return ""
    return str(item.get(f"option_{label.lower()}") or "").strip()


def build_drafts(br: BatchResult, ctx, profile: str = "default") -> tuple[list[QuestionDraft], dict[int, list[str]]]:
    cfg: Settings = ctx.cfg
    sizes = {p: (ctx.records[p].width, ctx.records[p].height) for p in br.page_nums}
    page_issues: dict[int, list[str]] = {}
    for p in br.failed_pages:
        page_issues.setdefault(p, []).append("parse_failed")
    regions: list[Region] = []
    hint = None
    for p in br.page_nums:
        W, H = sizes[p]
        pb = [b for b in br.blocks if b.page == p]
        mark_watermarks(pb, cfg, W, H)
        rs = segment_page(pb, W, H, cfg, p, br.batch_id, hint=(hint + 1) if hint else None)
        if not rs:
            leafs = [b for b in pb if b.type in ("text", "table_cell", "figure") and not b.watermark]
            page_issues.setdefault(p, []).append("no_questions_found" if len(leafs) >= 6 else "sparse_page")
        regions.extend(rs)
        if rs:
            hint = max(r.number for r in rs)
    for i, r in enumerate(regions):
        r.idx = i
    sem = run_semantics(ctx.client, br, regions, sizes) if regions else None
    if sem is not None:
        first = br.page_nums[0]
        for n in dict.fromkeys(sem.notes):
            page_issues.setdefault(first, []).append(n[:120])
        if sem.unmatched_items:
            page_issues.setdefault(first, []).append(f"extract_items_unmatched:{sem.unmatched_items}")
    drafts: list[QuestionDraft] = []
    for r in regions:
        W, H = sizes[r.page]
        layout = layout_region(r, cfg, W, H)
        item = sem.matched.get(r.idx) if sem else None
        d = QuestionDraft(region=r, layout=layout, page=r.page, column=r.column, number=r.number,
                          number_text=r.number_text, profile=profile)
        d.flags.extend(layout.flags)
        if item is None:
            d.flags.append("extract_missing")
        stem = str((item or {}).get("stem") or "").strip()
        d.stem_text = stem or layout.stem_fallback_text
        d.answer_text = " ".join(b.md for b in layout.answer_blocks if b.md).strip()
        ca = str((item or {}).get("correct_option") or "").strip().upper()[:1]
        d.correct_option = ca if ca in LABELS else ""
        # options (text from Extract, else fallback blocks)
        labels = set(layout.options) | {L for L in LABELS if _opt_text(item, L)}
        for L in sorted(labels):
            reg = layout.options.get(L)
            text = _opt_text(item, L) or (reg.fallback_text if reg else "")
            od = OptionDraft(label=L, text=text, box=reg.box if reg else None)
            if reg is not None:
                od.ref, od.image_only = option_visual(ctx.client, ctx.images, r.page, reg, text, cfg, d.flags)
            else:
                d.flags.append(f"option_{L}_without_region")
            if not od.text and od.ref is None:
                d.flags.append(f"option_{L}_empty")
            d.options[L] = od
        d.q_visuals = question_visuals(ctx.client, ctx.images, r.page, layout.stem_box, layout.stem_texts,
                                       layout.stem_figs, cfg, d.flags)
        toks = d.stem_text.split()
        if toks and ((len(toks[-1]) == 1 and toks[-1].isalpha() and toks[-1].lower() not in ("a", "i"))
                     or toks[-1].endswith(("-", "…"))):
            d.flags.append("stem_possibly_truncated")
        drafts.append(d)
    return drafts, page_issues


def materialize(drafts: list[QuestionDraft], ctx, assets_dir: Path) -> list[QuestionOut]:
    cfg: Settings = ctx.cfg
    drafts = sorted(drafts, key=lambda d: (d.page, d.column, d.region.box.y0))
    out: list[QuestionOut] = []
    for i, d in enumerate(drafts, 1):
        qid = f"q_{i:04d}"
        img = ctx.images.rgb(d.page)
        flags = list(dict.fromkeys(d.flags))
        qimgs = []
        for k, ref in enumerate(d.q_visuals, 1):
            a = save_crop(img, ref, d.page, assets_dir / qid / f"question_{k}.png", f"{qid}_img_{k}", cfg.asset_pad_px)
            qimgs.append(a)
        opts: dict[str, OptionOut] = {}
        for L, od in d.options.items():
            asset = None
            if od.ref is not None:
                asset = save_crop(img, od.ref, d.page, assets_dir / qid / f"option_{L}.png", f"{qid}_opt_{L}",
                                  cfg.asset_pad_px)
            opts[L] = OptionOut(text=od.text, image=asset, image_only=od.image_only)
        out.append(QuestionOut(qid=qid, number=d.number, number_text=d.number_text, page=d.page, column=d.column,
                               bbox_px=d.region.box.to_list(), text=d.stem_text,
                               correct_option=d.correct_option or None, answer_text=d.answer_text,
                               question_images=qimgs, options=opts, flags=flags,
                               ocr_confidence=d.ocr_conf, profile=d.profile))
    return out
