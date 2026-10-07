"""STAGE 1 - Parse canonical pages with Landing AI.

Canonical PNGs are bundled into batch PDFs (one parse call each) so every batch has ONE markdown,
ONE structure tree and consistent ranges for Extract/Ground. Page numbers inside a batch are
local (1..n); `page_nums` maps them back to global pages. Batch PDFs are split automatically
if they exceed the sync upload cap. Pages Landing AI reports as failed (HTTP 206) are retried
alone, as their own one-page batch.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..config import Settings
from ..models import PageRecord
from ..utils.io import write_json
from .structure import Block, flatten

log = logging.getLogger("pipeline.parse")
_batch_counter = itertools.count(1)


@dataclass
class BatchResult:
    batch_id: int
    page_nums: list[int]
    markdown: str
    structure: dict
    metadata: dict
    blocks: list[Block] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)      # GLOBAL page numbers


def build_batch_pdf(records: list[PageRecord]) -> bytes:
    """Lossless image-only PDF; each page has the pixel aspect ratio of its canonical PNG."""
    doc = pymupdf.open()
    for r in records:
        w, h = r.width * 72 / 300, r.height * 72 / 300
        page = doc.new_page(width=w, height=h)
        page.insert_image(pymupdf.Rect(0, 0, w, h), filename=r.canonical_path)
    data = doc.tobytes(deflate=True)
    doc.close()
    return data


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def parse_pages(client, records: list[PageRecord], cfg: Settings, raw_dir: Path | None = None,
                model: str | None = None, _retry_failed: int = 2) -> list[BatchResult]:
    results: list[BatchResult] = []
    sizes = {r.page_no: (r.width, r.height) for r in records}
    for group in _chunks(records, cfg.batch_pages):
        results.extend(_parse_group(client, group, sizes, cfg, raw_dir, model, _retry_failed))
    return results


def _parse_group(client, group, sizes, cfg, raw_dir, model, retry_failed) -> list[BatchResult]:
    pdf = build_batch_pdf(group)
    if len(pdf) > cfg.max_upload_mb * 1024 * 1024 and len(group) > 1:
        mid = len(group) // 2
        log.info("batch %.1f MB > cap; splitting %d pages", len(pdf) / 1e6, len(group))
        return (_parse_group(client, group[:mid], sizes, cfg, raw_dir, model, retry_failed)
                + _parse_group(client, group[mid:], sizes, cfg, raw_dir, model, retry_failed))
    page_nums = [r.page_no for r in group]
    bid = next(_batch_counter)
    out = client.parse(pdf, f"batch_{page_nums[0]:04d}_{page_nums[-1]:04d}.pdf", model=model)
    if raw_dir is not None:
        tag = (model or "pro").replace("/", "_")
        write_json(raw_dir / f"parse_{tag}_{page_nums[0]:04d}_{page_nums[-1]:04d}_b{bid}.json", out)
    failed_local = (out.get("metadata") or {}).get("failed_pages") or []
    failed_global = [page_nums[i - 1] for i in failed_local if 1 <= i <= len(page_nums)]
    br = BatchResult(batch_id=bid, page_nums=page_nums, markdown=out["markdown"], structure=out["structure"],
                     metadata=out.get("metadata") or {}, failed_pages=failed_global)
    br.blocks = flatten(out["structure"], out["markdown"], page_nums, sizes, bid)
    results = [br]
    if failed_global and retry_failed > 0:
        log.warning("pages %s failed to parse; retrying individually", failed_global)
        by_no = {r.page_no: r for r in group}
        for p in failed_global:
            results.extend(_parse_group(client, [by_no[p]], sizes, cfg, raw_dir, model, retry_failed - 1))
        br.failed_pages = []
    return results
