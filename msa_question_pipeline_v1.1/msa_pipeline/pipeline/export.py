"""Build, validate and commit the canonical v2 output of one document.

Layout of a document directory (all paths inside document.json are relative to it):
    data/<doc_id>/document.json            canonical questions + asset registry      (schema: pipeline/schema.py)
    data/<doc_id>/image_descriptions.txt   one entry per asset (generated from document.json)
    data/<doc_id>/assets/<qid>/stem_1.png, opt_B_1.png, opt_B_2.png ...
    data/<doc_id>/output/run_report.json   processing statistics, page issues, review list (not needed to rebuild)

Commit protocol (no half-written state is ever visible as "the" output):
    1. crops are written into assets.staging/ (each file atomically)
    2. document.json is built, schema-validated and integrity-checked against assets.staging/
    3. swap: assets -> assets.old, assets.staging -> assets, then document.json + manifest are replaced atomically
    4. assets.old is deleted.  A crash before 3 leaves the previous output untouched; leftovers of an interrupted
       run (assets.staging / assets.old) are cleaned at the start of the next run of that document.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .models import AssetRef, QuestionOut
from .schema import (Answer, Asset, Document, EquationComponent, ImageComponent, Option, Owner, Question,
                     SourceDoc, SourceRef, TableComponent, TextComponent)
from .stages.validate import INFO_FLAGS
from .utils.io import atomic_write_bytes, sha256_file, write_json

STAGING, OLD = "assets.staging", "assets.old"
_BLOCK = re.compile(r"(\$\$.+?\$\$|<table\b.*?</table>)", re.S | re.I)
_PROTECTED = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$|<table\b.*?</table>|<(sub|sup)>.*?</\1>", re.S | re.I)


def prepare(doc_dir: Path) -> Path:
    """Clean leftovers of an interrupted run and return an empty staging directory."""
    for name in (STAGING, OLD):
        shutil.rmtree(doc_dir / name, ignore_errors=True)
    staging = doc_dir / STAGING
    staging.mkdir(parents=True, exist_ok=True)
    return staging


# ---------------- text -> ordered components ----------------
def split_components(text: str) -> list:
    out: list = []
    for part in _BLOCK.split(text or ""):
        if not part or not part.strip():
            continue
        if part.startswith("$$") and part.endswith("$$") and len(part) > 4:
            out.append(EquationComponent(latex=part[2:-2].strip()))
        elif part.lower().startswith("<table"):
            out.append(TableComponent(html=part.strip()))
        else:
            out.append(TextComponent(text=part.strip()))
    return out


def _safe_cuts(text: str) -> list[int]:
    blocked = [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]
    cuts = [0, len(text)]
    for i, ch in enumerate(text):
        if ch.isspace() and not any(a < i < b for a, b in blocked):
            cuts.append(i)
    return sorted(set(cuts))


def compose(text: str, images: list[tuple[float, str]]) -> list:
    """Interleave text and images: each image is inserted where `pos` (fraction of text before it) says, snapped
    to a whitespace boundary that does not split math, a table or a sub/superscript."""
    if not images:
        return split_components(text)
    cuts = _safe_cuts(text)
    placed: dict[int, list[str]] = {}
    for pos, aid in images:
        if pos <= 0:
            at = 0
        elif pos >= 1:
            at = len(text)
        else:
            target = pos * len(text)
            at = min(cuts, key=lambda c: (abs(c - target), c))
        placed.setdefault(at, []).append(aid)
    out: list = []
    prev = 0
    for at in sorted(placed):
        out += split_components(text[prev:at])
        out += [ImageComponent(asset_id=a) for a in placed[at]]
        prev = at
    out += split_components(text[prev:])
    return out


# ---------------- QuestionOut -> v2 ----------------
def _norm(box: list[float] | None, size: tuple[int, int]) -> list[float] | None:
    if box is None:
        return None
    W, H = size
    x0, y0, x1, y1 = box
    c = lambda v, m: round(min(1.0, max(0.0, v / m)), 5)
    return [c(x0, W), c(y0, H), c(x1, W), c(y1, H)]


def _rel(path: str, staging: Path) -> str:
    return "assets/" + Path(os.path.relpath(path, staging)).as_posix()


def build_document(doc_id: str, pdf_path: Path, page_count: int, pages: list[int], questions: list[QuestionOut],
                   page_sizes: dict[int, tuple[int, int]], staging: Path) -> Document:
    assets: dict[str, Asset] = {}
    out_q: list[Question] = []

    def register(a: AssetRef, owner: Owner) -> tuple[float, str]:
        assets[a.asset_id] = Asset(path=_rel(a.path, staging), sha256=a.sha256, width=a.width, height=a.height,
                                   page=a.page, bbox=_norm(a.bbox_px, page_sizes[a.page]), owner=owner,
                                   method=a.method, certain=a.certain, description=a.description)
        return a.pos, a.asset_id

    for q in questions:
        flags = [f for f in q.flags if f not in INFO_FLAGS]
        stem_imgs = [a for a in q.question_images if a.path and a.width]
        if any(not a.pos_exact for a in stem_imgs):
            flags.append("component_order_estimated")
        stem = compose(q.text, [register(a, Owner(qid=q.qid, role="stem")) for a in stem_imgs])
        options = []
        for L in sorted(q.options):
            o = q.options[L]
            imgs = [a for a in o.images if a.path and a.width]
            if any(not a.pos_exact for a in imgs):
                flags.append("component_order_estimated")
            options.append(Option(label=L, content=compose(
                o.text, [register(a, Owner(qid=q.qid, role="option", option=L)) for a in imgs])))
        answer = Answer(label=q.correct_option, source=q.answer_source or "printed_inline") \
            if q.correct_option in ("A", "B", "C", "D", "E") else None
        out_q.append(Question(qid=q.qid, number=q.number or None, section=q.section,
                              source=SourceRef(pages=[q.page], bbox=_norm(q.bbox_px, page_sizes[q.page])),
                              stem=stem, options=options, answer=answer, confidence=q.confidence,
                              flags=list(dict.fromkeys(flags))))
    return Document(doc_id=doc_id, source=SourceDoc(filename=Path(pdf_path).name, sha256=sha256_file(Path(pdf_path)),
                                                    page_count=page_count, pages_processed=sorted(pages)),
                    questions=out_q, assets=assets)


# ---------------- manifest ----------------
def manifest_text(doc: Document) -> str:
    """image_descriptions.txt: one delimited entry per asset, generated from document.json (UTF-8)."""
    by_q = {q.qid: q for q in doc.questions}
    lines = [f"# image descriptions for {doc.doc_id} ({doc.source.filename}) - {len(doc.assets)} image(s)",
             "# generated from document.json; one entry per asset id", ""]
    for aid in sorted(doc.assets):
        a = doc.assets[aid]
        q = by_q.get(a.owner.qid)
        role = "stem" if a.owner.role == "stem" else f"option {a.owner.option}"
        lines += [f"=== {aid} ===",
                  f"file: {a.path}",
                  f"page: {a.page}",
                  f"question: {a.owner.qid}" + (f" (number {q.number})" if q and q.number else ""),
                  f"role: {role}",
                  f"association: {'certain' if a.certain else 'uncertain'}",
                  f"method: {a.method}",
                  "description:",
                  a.description or "(none produced by Landing AI)",
                  "=== end ===", ""]
    return "\n".join(lines)


# ---------------- commit ----------------
def commit(doc_dir: Path, doc: Document, report: dict) -> None:
    from .reconstruct import verify
    problems = verify(doc, doc_dir, assets_dir=doc_dir / STAGING)
    if problems:
        raise RuntimeError("refusing to commit inconsistent output:\n  " + "\n  ".join(problems[:20]))
    assets, staging, old = doc_dir / "assets", doc_dir / STAGING, doc_dir / OLD
    if assets.exists():
        os.replace(assets, old)
    os.replace(staging, assets)
    atomic_write_bytes(doc_dir / "document.json", doc.model_dump_json(indent=2).encode("utf-8"))
    atomic_write_bytes(doc_dir / "image_descriptions.txt", manifest_text(doc).encode("utf-8"))
    write_json(doc_dir / "output" / "run_report.json", report)
    shutil.rmtree(old, ignore_errors=True)
