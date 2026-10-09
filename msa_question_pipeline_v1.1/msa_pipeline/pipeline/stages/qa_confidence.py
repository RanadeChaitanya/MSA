"""Optional QA pass: re-parse risky pages with DPT-3 Verity (word-level confidence) and attach
a per-question OCR confidence. Pro has no confidence scores; Verity does (words in text/tables)."""
from __future__ import annotations

import logging

import numpy as np

from .parse_stage import parse_pages

log = logging.getLogger("pipeline.qa")


def run_verity_qa(ctx, drafts_by_page: dict[int, list], pages: list[int]) -> None:
    if not pages:
        return
    recs = [ctx.records[p] for p in pages]
    brs = parse_pages(ctx.client, recs, ctx.cfg, ctx.paths.lat_raw, model=ctx.cfg.verity_model, src_pdf=ctx.pdf_path)
    words: dict[int, list[tuple[float, float, float]]] = {p: [] for p in pages}
    for br in brs:
        for b in br.blocks:
            for a in b.atomic:
                if a.get("conf") is not None and b.page in words:
                    words[b.page].append((a["box"].cx, a["box"].cy, float(a["conf"])))
    for p in pages:
        for d in drafts_by_page.get(p, []):
            cs = sorted(c for cx, cy, c in words[p] if d.region.box.contains_point(cx, cy))
            if cs:
                low = cs[:max(1, len(cs) // 10)]
                d.ocr_conf = round(float(np.mean(low)), 3)
