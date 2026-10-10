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
from .retry.ladder import SkipRung, run_ladder
from .stages.assemble import QuestionDraft, build_drafts, materialize
from .stages.canonicalize import canonicalize_page, canonicalize_pdf
from .stages.parse_stage import _batch_counter, parse_pages
from .stages.pdf_text import build_pdf_batch
from .stages.qa_confidence import run_verity_qa
from .stages.validate import PAGE_ERRORS, error_count, is_error, validate_drafts, validate_final
from .triage import triage
from . import export
from .stages.visuals import ImageCache
import os

from .utils.io import sha256_file, slug, write_json

log = logging.getLogger("pipeline.runner")
_REVIEW = ("low_ocr_confidence", "stem_possibly_truncated", "option_order_uncertain")   # review, never a paid retry


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
    page_kinds: dict = field(default_factory=dict)      # triage kind per page
    retry_parse: set = field(default_factory=set)       # PDF-text pages the local split could not verify


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
    # cost cut B: born-digital question pages are read from the PDF text layer (no Parse call)
    digital = [p for p in page_nums if ctx.cfg.pdf_text and ctx.page_kinds.get(p) == "question_digital"
               and ctx.records[p].source == "render" and ctx.records[p].profile == "default"]
    # cost cut C: screenshot / scanned question pages read by local Docling OCR first (no Parse call)
    dl_pages = [p for p in page_nums if ctx.cfg.docling and p not in digital and ctx.records[p].source == "render"
                and ctx.page_kinds.get(p) in ("question_composite", "question_scanned")]
    recs = [ctx.records[p] for p in page_nums if p not in digital and p not in dl_pages]
    brs = parse_pages(ctx.client, recs, ctx.cfg, ctx.paths.lat_raw, src_pdf=ctx.pdf_path) if recs else []
    sizes = {p: (ctx.records[p].width, ctx.records[p].height) for p in digital}
    to_parse: list[int] = []
    pdf_brs = []
    for k in range(0, len(digital), ctx.cfg.batch_pages):
        group = digital[k:k + ctx.cfg.batch_pages]
        br, unsafe = build_pdf_batch(ctx.pdf_path, group, sizes, next(_batch_counter))
        to_parse += unsafe
        if br.page_nums:
            pdf_brs.append(br)
    if dl_pages:
        from .stages.docling_text import build_docling_batch
        dsz = {p: (ctx.records[p].width, ctx.records[p].height) for p in dl_pages}
        for k in range(0, len(dl_pages), ctx.cfg.batch_pages):
            br, unsafe = build_docling_batch(ctx.pdf_path, dl_pages[k:k + ctx.cfg.batch_pages], dsz,
                                             next(_batch_counter), Path(ctx.cfg.data_dir) / "cache" / "docling")
            to_parse += unsafe
            if br.page_nums:
                pdf_brs.append(br)
    out: dict[int, tuple[list[QuestionDraft], list[str]]] = {p: ([], []) for p in page_nums}

    def collect(batch_results, skip: set[int]):
        for br in batch_results:
            drafts, issues = build_drafts(br, ctx)
            for d in drafts:
                if d.page not in skip:
                    out[d.page][0].append(d)
            for p, iss in issues.items():
                if p not in skip:
                    out.setdefault(p, ([], []))[1].extend(iss)

    collect(brs, set())
    ctx.retry_parse = set()
    collect(pdf_brs, set())
    retry = sorted(set(to_parse) | ctx.retry_parse)
    if retry:                                         # PDF text not provable for these pages: read them with Parse
        for p in ctx.retry_parse:
            out[p] = ([], [])
        log.info("pages read by Parse instead of the PDF text layer: %s", retry)
        collect(parse_pages(ctx.client, [ctx.records[p] for p in retry], ctx.cfg, ctx.paths.lat_raw,
                            src_pdf=ctx.pdf_path), set())
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

    # ---- free local triage: which pages hold questions, chapters, printed answer keys (no API calls)
    tri = triage(pdf_path)
    skipped: dict[int, str] = {}
    if pages is None:
        pages = tri.question_pages()
        skipped = {p: i.kind for p, i in tri.pages.items() if not i.is_question}
        log.info("triage: %d question page(s), %d skipped (%s)", len(pages), len(skipped), tri.summary()["kinds"])

    # ---- stage 0
    records_list = canonicalize_pdf(pdf_path, paths.canonical, cfg, pages=pages) if pages else []
    records = {r.page_no: r for r in records_list}
    if stop_after == "canonicalize" or not records:
        write_json(paths.output / "run_report.json", {"pages": [r.model_dump() for r in records_list],
                                                      "skipped_pages": skipped})
        return DocumentOut(doc_id=doc_id, source_pdf=str(pdf_path), page_count=len(records_list),
                           pages=records_list, questions=[])
    client = client or LatClient(cfg)
    ctx = Ctx(cfg=cfg, client=client, paths=paths, records=records, pdf_path=pdf_path,
              page_kinds={p: i.kind for p, i in tri.pages.items()})
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
            # Rendered digital pages are already pixel-perfect: only a higher-resolution render can help, and only
            # when the page was flagged blurry/small. This stops the ladder burning credits on no-op rungs.
            if old.source == "render" and (profile != "hires" or not ({"low_sharpness", "low_resolution"} & set(old.flags))):
                raise SkipRung("rendered page; profile cannot change the image")
            rec = canonicalize_page(doc, p - 1, paths.canonical, cfg, profile)
            if sha256_file(Path(rec.canonical_path)) == sha256_file(Path(old.canonical_path)):
                raise SkipRung("identical canonical image")
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

    # ---- numbering gap check across the whole document after ladder, then materialise into a staging area
    all_drafts = [d for p in page_nums for d in drafts_by_page[p]]
    validate_drafts(all_drafts)
    staging = export.prepare(paths.root)
    questions = materialize(all_drafts, ctx, staging)
    for q in questions:                                   # chapter + printed answer key (local, free)
        chap = tri.chapter_of_page.get(q.page)
        q.section = chap if chap and not chap.startswith("part ") else None
        key = tri.keys.get(chap or "", {}) if q.section else {}
        if q.number in key:
            # the chapter's printed key wins: Extract maps any answer table in the batch by number alone and
            # was observed taking another chapter's key
            q.correct_option, q.answer_source = key[q.number], "answer_key"
        elif q.correct_option and tri.keys:
            q.correct_option, q.answer_source = None, None   # keys exist, none for this chapter: don't guess
    sizes = {p: (r.width, r.height) for p, r in ctx.records.items()}
    validate_final(questions, sizes, cfg)

    issue_pages = {p for p, iss in issues_by_page.items() if any(i in PAGE_ERRORS for i in iss)}
    issue_pages |= {q.page for q in questions if any(is_error(f) for f in q.flags)}
    overlays: list[str] = []
    if cfg.debug_overlays and (issue_pages or cfg.overlays_all):
        overlay_pages = page_nums if cfg.overlays_all else sorted(issue_pages)
        overlays = draw_overlays(ctx, {p: drafts_by_page[p] for p in overlay_pages},
                                 {p: issues_by_page[p] for p in overlay_pages}, paths.debug / "overlays")

    review = [{"qid": q.qid, "number": q.number, "page": q.page, "confidence": q.confidence,
               "flags": [f for f in q.flags if is_error(f) or f in _REVIEW]}
              for q in questions
              if any(is_error(f) for f in q.flags) or q.confidence < 0.7 or any(f in _REVIEW for f in q.flags)]
    bad_pages = {str(p): [i for i in iss if i] for p, iss in issues_by_page.items() if iss}
    run = {"seconds": round(time.time() - t0, 1), "questions": len(questions), "needs_review": len(review),
           "credits": round(getattr(client, "credits", 0.0), 2), "api_calls": dict(getattr(client, "calls", {})),
           "cache_hits": getattr(client, "cache_hits", 0), "overlay_count": len(overlays)}
    with pymupdf.open(str(pdf_path)) as _d:
        n_pages = _d.page_count
    document = export.build_document(doc_id, pdf_path, n_pages, page_nums, questions, sizes, staging)
    report = {"doc_id": doc_id, "run": run, "page_issues": bad_pages, "review": review,
              "skipped_pages": {str(p): k for p, k in skipped.items()},
              "pages": [{"page": p, "source": ctx.records[p].source, "profile": ctx.records[p].profile,
                         "kind": tri.pages[p].kind, "flags": ctx.records[p].flags} for p in page_nums]}
    export.commit(paths.root, document, report)
    for q in questions:                                   # in-memory result now points at the committed files
        for a in list(q.question_images) + [a for o in q.options.values() for a in o.images]:
            if a.path:
                a.path = export._rel(a.path, staging)
    doc_out = DocumentOut(doc_id=doc_id, source_pdf=str(pdf_path), page_count=len(records),
                          pages=[ctx.records[p] for p in page_nums], questions=questions, page_issues=bad_pages, run=run)
    log.info("done: %d questions, %d need review, credits=%.2f", len(questions), len(review), run["credits"])
    return doc_out


def _rel(path: str, root: Path) -> str:
    if not path:
        return path
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:                       # different drive on Windows
        return Path(path).as_posix()
