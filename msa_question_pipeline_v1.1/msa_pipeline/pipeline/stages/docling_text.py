"""Cost cut C: page text from Docling (local layout + OCR, no API call) for picture-based question pages.

Same contract as pdf_text.build_pdf_batch: returns a BatchResult of the pages Docling can carry and the pages that
must go to Landing AI Parse instead. A page goes to Parse when Docling sees a picture / table / chart on it (image
questions stay on Landing AI, which crops and describes them) or when it yields no text. Pages it does carry are then
verified by the same local split checks as every other page; a page that fails is re-read by Parse (runner).
Docling output is cached on disk per (pdf, page): OCR takes ~3 min per page on CPU.

Measured on the 137 ground-truth questions (2026-10-10, docling 2.137 + RapidOCR, CPU): 0 of 16 screenshot /
scanned pages passed the local checks (6 had pictures; on 10 the OCR lost question numbers or misread option labels,
e.g. "C)" -> "0", 10^22 -> "1022"), so every page fell back to Parse: no saving, +~50 min CPU. Off by default
(DOCLING=1); docling is not in requirements.txt (pip install docling to try it).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..utils.geometry import Box
from ..utils.io import sha256_file
from .parse_stage import BatchResult
from .pdf_text import page_markdown
from .structure import Block

log = logging.getLogger("pipeline.docling")

VISUAL = {"picture", "table", "chart"}
SKIP = {"page_header", "page_footer"}
_conv = None


def _converter():
    global _conv
    if _conv is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        o = PdfPipelineOptions(do_ocr=True, do_table_structure=False)
        o.ocr_options.force_full_page_ocr = True        # screenshot pages: the text layer holds only the numbers
        _conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=o)})
    return _conv


def page_items(pdf_path: Path, page: int, cache_dir: Path) -> dict:
    """{"w", "h", "items": [{"label", "text", "bbox": [l, t, r, b] top-left origin, PDF points}]} for one page."""
    key = cache_dir / f"{sha256_file(pdf_path)[:16]}_p{page:04d}.json"
    if key.exists():
        return json.loads(key.read_text(encoding="utf-8"))
    doc = _converter().convert(str(pdf_path), page_range=(page, page)).document
    pg = doc.pages[min(doc.pages)]
    out = {"w": pg.size.width, "h": pg.size.height, "items": []}
    for item, _ in doc.iterate_items():
        for prov in getattr(item, "prov", None) or []:
            bb = prov.bbox.to_top_left_origin(pg.size.height)
            out["items"].append({"label": str(getattr(item.label, "value", item.label)),
                                 "text": getattr(item, "text", "") or "", "bbox": [bb.l, bb.t, bb.r, bb.b]})
    cache_dir.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def build_docling_batch(pdf_path: Path, page_nums: list[int], sizes: dict[int, tuple[int, int]], batch_id: int,
                        cache_dir: Path) -> tuple[BatchResult, list[int]]:
    md = ""
    blocks: list[Block] = []
    safe: list[int] = []
    unsafe: list[int] = []
    for p in page_nums:
        W, H = sizes[p]
        pg = page_items(pdf_path, p, cache_dir)
        sx, sy = W / pg["w"], H / pg["h"]
        pb: list[Block] = []
        visual = False
        for k, it in enumerate(pg["items"]):
            if it["label"] in SKIP:
                continue
            l, t, r, b = it["bbox"]
            box = Box(l * sx, t * sy, r * sx, b * sy)
            if it["label"] in VISUAL:
                visual = True
                continue
            if it["text"].strip():
                pb.append(Block(uid=f"dl{batch_id}/{p}-{k}", id=f"dl-{k}", type="text", page=p, local_page=1,
                                box=box, md=it["text"], plain=it["text"], rng=(-1, -1), batch_id=batch_id))
        if visual or not pb:
            unsafe.append(p)          # image question on the page (or nothing read): Landing AI Parse
            continue
        if safe:
            md += "\n<!-- PAGE BREAK -->\n\n"
        md += page_markdown(pb, W, len(md))
        blocks.extend(pb)
        safe.append(p)
    br = BatchResult(batch_id=batch_id, page_nums=safe, markdown=md,
                     structure={"children": []}, metadata={"source": "docling"}, blocks=blocks)
    return br, unsafe
