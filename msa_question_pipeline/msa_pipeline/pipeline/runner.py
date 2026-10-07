"""Orchestrator: PDF -> questions.json (+ assets, overlays, review queue, run report)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pymupdf

from .config import Settings
from .debug.overlay import draw_overlays
from .lat.client import LatClient
from .models import DocumentOut, PageRecord
from .retry.ladder import run_ladder
from .stages.assemble import QuestionDraft, build_drafts, materialize
from .stages.canonicalize import canonicalize_page, canonicalize_pdf
from .stages.parse_stage import parse_pages
from .stages.qa_confidence import run_verity_qa
from .stages.validate import error_count, is_error, validate_drafts, validate_final
from .stages.visuals import ImageCache
from .utils.io import sha256_file, slug, write_json

log = logging.getLogger("pipeline.runner")


@dataclass
class DocPaths:
    root: Path
    canonical: Path
    lat_raw: Path
    assets: Path
    output: Path
    debug: Path

    @classmethod
    def create(cls, data_dir: Path, doc_id: str) -> "DocPaths":
        root = Path(data_dir) / doc_id
        p = cls(root, root / "canonical", root / "lat_raw", root / "assets", root / "output", root / "debug")
        for d in (p.canonical, p.lat_raw, p.assets, p.output, p.debug):
            d.mkdir(parents=True, exist_ok=True)
        return p


@dataclass
class Ctx:
    cfg: Settings
    client: object
    paths: DocPaths
    records: dict[int, PageRecord]
    images: ImageCache = None            # type: ignore[assignment]
    pdf_path: Path = None                # type: ignore[assignment]


def parse_page_spec(spec: Optional[str], n_pages: int) -> Optional[list[int]]:
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted({p for p in out if 1 <= p <= n_pages})


def process_pages(ctx: Ctx, page_nums: list[int]) -> dict[int, tuple[list[QuestionDraft], list[str]]]:
    recs = [ctx.records[p] for p in page_nums]
    brs = parse_pages(ctx.client, recs, ctx.cfg, ctx.paths.lat_raw)
    out: dict[int, tuple[list[QuestionDraft], list[str]]] = {p: ([], []) for p in page_nums}
    for br in brs:
        drafts, issues = build_drafts(br, ctx)
        for d in drafts:
            out[d.page][0].append(d)
        for p, iss in issues.items():
            out.setdefault(p, ([], []))[1].extend(iss)
    for p in page_nums:
        validate_drafts(out[p][0])
    return out


def process_document(pdf_path: Path, cfg: Settings, client=None, doc_id: Optional[str] = None,
                     pages: Optional[list[int]] = None, stop_after: Optional[str] = None) -> DocumentOut:
    t0 = time.time()
    pdf_path = Path(pdf_path)
    doc_id = doc_id or f"{slug(pdf_path.stem)}_{sha256_file(pdf_path)[:8]}"
    paths = DocPaths.create(cfg.data_dir, doc_id)
    log.info("doc_id=%s", doc_id)

    # ---- stage 0
    records_list = canonicalize_pdf(pdf_path, paths.canonical, cfg, pages=pages)
    records = {r.page_no: r for r in records_list}
    write_json(paths.output / "pages.json", [r.model_dump() for r in records_list])
    if stop_after == "canonicalize":
        return DocumentOut(doc_id=doc_id, source_pdf=str(pdf_path), page_count=len(records_list),
                           pages=records_list, questions=[])
    client = client or LatClient(cfg)
    ctx = Ctx(cfg=cfg, client=client, paths=paths, records=records, pdf_path=pdf_path)
    ctx.images = ImageCache(ctx.records)

    # ---- stages 1-5 (+ validation)
    page_nums = sorted(records)
    state: dict[int, tuple] = {}
    for p, (drafts, issues) in process_pages(ctx, page_nums).items():
        state[p] = (drafts, issues, ctx.records[p])

    # ---- retry ladder on failing pages
    def page_score(drafts, issues):
        return error_count(drafts, issues)

    failing = [p for p in page_nums if page_score(state[p][0], state[p][1]) > 0]
    if failing:
        log.info("ladder on %d failing page(s): %s", len(failing), failing[:cfg.ladder_max_pages])
    if failing and cfg.ladder_profiles:
        doc = pymupdf.open(str(pdf_path))

        def attempt(p: int, profile: str):
            old = ctx.records[p]
            rec = canonicalize_page(doc, p - 1, paths.canonical, cfg, profile)
            ctx.records[p] = rec
            ctx.images.invalidate(p)
            try:
                drafts, issues = process_pages(ctx, [p])[p]
            finally:
                ctx.records[p] = old
                ctx.images.invalidate(p)
            return drafts, issues, rec

        try:
            run_ladder(failing[:cfg.ladder_max_pages], cfg.ladder_profiles, attempt, page_score, state)
        finally:
            doc.close()
    for p in page_nums:                                  # settle records on the accepted attempt
        ctx.records[p] = state[p][2]
        ctx.images.invalidate(p)
    drafts_by_page = {p: state[p][0] for p in page_nums}
    issues_by_page = {p: state[p][1] for p in page_nums}
    for p in page_nums:
        for d in drafts_by_page[p]:
            d.profile = ctx.records[p].profile

    # ---- optional OCR-confidence QA (Verity)
    qa_pages = page_nums if cfg.qa_verity else [p for p in page_nums if "low_sharpness" in ctx.records[p].flags]
    if qa_pages:
        try:
            run_verity_qa(ctx, drafts_by_page, qa_pages)
        except Exception as e:
            log.warning("verity QA skipped: %s", e)

    # ---- numbering gap check across the whole document after ladder, then materialise
    all_drafts = [d for p in page_nums for d in drafts_by_page[p]]
    validate_drafts(all_drafts)
    questions = materialize(all_drafts, ctx, paths.assets)
    validate_final(questions, {p: (r.width, r.height) for p, r in ctx.records.items()}, cfg)

    overlays: list[str] = []
    if cfg.debug_overlays:
        overlays = draw_overlays(ctx, drafts_by_page, issues_by_page, paths.debug / "overlays")

    review = [{"qid": q.qid, "number": q.number, "page": q.page, "confidence": q.confidence,
               "flags": [f for f in q.flags if is_error(f) or f in ("low_ocr_confidence", "stem_possibly_truncated")]}
              for q in questions
              if any(is_error(f) for f in q.flags) or q.confidence < 0.7
              or "stem_possibly_truncated" in q.flags]
    bad_pages = {str(p): [i for i in iss if i] for p, iss in issues_by_page.items() if iss}
    doc_out = DocumentOut(
        doc_id=doc_id, source_pdf=str(pdf_path), page_count=len(records), pages=[ctx.records[p] for p in page_nums],
        questions=questions, page_issues=bad_pages,
        run={"seconds": round(time.time() - t0, 1), "questions": len(questions), "needs_review": len(review),
             "credits": round(getattr(client, "credits", 0.0), 2), "api_calls": dict(getattr(client, "calls", {})),
             "cache_hits": getattr(client, "cache_hits", 0), "overlays": overlays})
    write_json(paths.output / "questions.json", doc_out.model_dump())
    write_json(paths.output / "review_queue.json", {"questions": review, "page_issues": bad_pages})
    log.info("done: %d questions, %d need review, credits=%.2f", len(questions), len(review), doc_out.run["credits"])
    return doc_out
