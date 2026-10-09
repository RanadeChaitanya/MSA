"""STAGE 8 - Validation. Flags are strings; ERROR flags trigger the retry ladder / review queue."""
from __future__ import annotations

import re
from pathlib import Path

from ..config import Settings
from ..models import QuestionOut
from .assemble import LABELS, QuestionDraft

ERROR_EXACT = {"too_few_options", "options_not_sequential", "no_option_anchors", "asset_missing", "asset_too_small",
               "figure_double_role", "numbering_gap", "image_only_option_without_image", "asset_out_of_bounds"}
ERROR_RE = re.compile(r"^option_[A-D]_empty$")
PAGE_ERRORS = {"parse_failed"}
# Informational flags: they describe HOW something was derived and never reduce confidence.
INFO_FLAGS = {"layout_from_spans", "region_from_spans", "ink_unconfirmed_question", "figure_found_by_repass",
              "grounded_by_text_match", "options_from_table_cells"}


def is_error(flag: str) -> bool:
    return flag in ERROR_EXACT or bool(ERROR_RE.match(flag))


def _add(flags: list[str], f: str) -> None:
    if f not in flags:
        flags.append(f)


def validate_drafts(drafts: list[QuestionDraft]) -> None:
    """Structural checks on drafts (no files needed)."""
    for d in drafts:
        labels = sorted(d.options)
        if len(labels) < 2:
            _add(d.flags, "too_few_options")
        elif labels != LABELS[:len(labels)]:
            _add(d.flags, "options_not_sequential")
        if 2 <= len(labels) < 4:
            _add(d.flags, "fewer_than_4_options")
        seen: dict[str, str] = {}
        refs = [("q", r) for r in d.q_visuals] + [(f"opt_{L}", o.ref) for L, o in d.options.items() if o.ref]
        for role, r in refs:
            for sid in r.source_ids:
                if sid in seen and seen[sid] != role:
                    _add(d.flags, "figure_double_role")
                seen[sid] = role
        for i, (ra, a) in enumerate(refs):
            for rb, b in refs[i + 1:]:
                if ra != rb and a.box.iou(b.box) > 0.5:
                    _add(d.flags, "figure_double_role")
    ordered = sorted(drafts, key=lambda d: (d.page, d.order_idx, d.column, d.region.box.y0))
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.number != prev.number + 1 and cur.number != 1:
            _add(prev.flags, "numbering_gap")
            _add(cur.flags, "numbering_gap")
    dup = {}
    for d in ordered:
        dup.setdefault(d.number, []).append(d)
    for n, ds in dup.items():
        if len(ds) > 1 and n != 1:
            for d in ds:
                _add(d.flags, "numbering_gap")


def error_count(drafts: list[QuestionDraft], issues: list[str]) -> int:
    n = sum(1 for d in drafts for f in set(d.flags) if is_error(f))
    n += sum(1 for i in issues if i in PAGE_ERRORS)
    return n


def validate_final(questions: list[QuestionOut], page_sizes: dict[int, tuple[int, int]], cfg: Settings,
                   root: Path | None = None) -> None:
    for q in questions:
        W, H = page_sizes.get(q.page, (10 ** 9, 10 ** 9))
        assets = list(q.question_images) + [o.image for o in q.options.values() if o.image]
        for a in assets:
            ap = Path(a.path) if (a.path and (root is None or Path(a.path).is_absolute())) else (root / a.path if a.path else None)
            if ap is None or not ap.exists() or a.width == 0:
                _add(q.flags, "asset_missing")
            elif min(a.width, a.height) < cfg.min_asset_px:
                _add(q.flags, "asset_too_small")
            x0, y0, x1, y1 = a.bbox_px
            if x1 > W + 1 or y1 > H + 1 or x0 < -1 or y0 < -1:
                _add(q.flags, "asset_out_of_bounds")
        for L, o in q.options.items():
            if o.image_only and not o.image:
                _add(q.flags, "image_only_option_without_image")
        if q.ocr_confidence is not None and q.ocr_confidence < 0.8:
            _add(q.flags, "low_ocr_confidence")
        errs = sum(1 for f in q.flags if is_error(f))
        warns = sum(1 for f in q.flags if not is_error(f) and f not in INFO_FLAGS)
        conf = 1.0 - 0.25 * errs - 0.05 * warns
        if q.ocr_confidence is not None:
            conf = min(conf, 0.5 + 0.5 * q.ocr_confidence)
        q.confidence = round(max(0.0, min(1.0, conf)), 3)
