"""Reading side of the v2 contract: load, verify and deterministically rebuild questions.

CONSTRUCTOR INPUT CONTRACT (what a future question constructor may rely on)
  input : a document directory D containing D/document.json (pipeline/schema.py, schema_version "2.0")
          and the files under D/assets/ that it references; one `qid`.
  steps : 1. doc = Document.model_validate_json(D/document.json)          (strict: unknown fields are errors)
          2. q = the question with that qid (unique)
          3. stem  = q.stem in list order;   options = q.options in list order (labels A, B, C, D)
          4. each {"type":"image","asset_id":X}  ->  doc.assets[X]; file = D / asset.path; check sha256;
             asset.owner must be (q.qid, "stem") for stem images and (q.qid, "option", label) for option images
          5. text components are md_latex (inline $..$, <sub>/<sup>); equation = display LaTeX; table = HTML
  guarantees (enforced by verify() before any output is committed):
          * every image component resolves to an existing file with the recorded sha256
          * every asset is referenced exactly once, from the place its owner names
          * qids are unique; option labels are unique and in order
  Nothing else in the directory is needed to rebuild a question.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .schema import Document, ImageComponent

ORDER = ["A", "B", "C", "D", "E"]


def load_document(doc_dir: Path) -> Document:
    return Document.model_validate_json((Path(doc_dir) / "document.json").read_text(encoding="utf-8"))


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(doc: Document, doc_dir: Path, assets_dir: Optional[Path] = None) -> list[str]:
    """Referential integrity. `assets_dir` lets the writer verify a staging directory before it is swapped in."""
    doc_dir = Path(doc_dir)
    base = Path(assets_dir) if assets_dir is not None else doc_dir / "assets"
    problems: list[str] = []
    qids = [q.qid for q in doc.questions]
    for dup in sorted({x for x in qids if qids.count(x) > 1}):
        problems.append(f"duplicate qid {dup}")
    refs: dict[str, list[tuple[str, str, Optional[str]]]] = {}
    for q in doc.questions:
        labels = [o.label for o in q.options]
        if labels != sorted(labels, key=ORDER.index) or len(set(labels)) != len(labels):
            problems.append(f"{q.qid}: option labels not unique/in order: {labels}")
        for c in q.stem:
            if isinstance(c, ImageComponent):
                refs.setdefault(c.asset_id, []).append((q.qid, "stem", None))
        for o in q.options:
            for c in o.content:
                if isinstance(c, ImageComponent):
                    refs.setdefault(c.asset_id, []).append((q.qid, "option", o.label))
    for aid, where in refs.items():
        a = doc.assets.get(aid)
        if a is None:
            problems.append(f"{aid}: referenced but not in assets")
            continue
        if len(where) > 1:
            problems.append(f"{aid}: referenced {len(where)} times")
        if (a.owner.qid, a.owner.role, a.owner.option) != where[0]:
            problems.append(f"{aid}: owner {a.owner.model_dump()} != referenced from {where[0]}")
    for aid, a in doc.assets.items():
        if aid not in refs:
            problems.append(f"{aid}: orphan asset (not referenced)")
        f = base / a.path[len("assets/"):]
        if not f.is_file():
            problems.append(f"{aid}: file missing {a.path}")
        elif _sha(f) != a.sha256:
            problems.append(f"{aid}: sha256 mismatch for {a.path}")
    if base.is_dir():                                    # files on disk that nothing references
        known = {a.path[len("assets/"):] for a in doc.assets.values()}
        for f in base.rglob("*.png"):
            if f.relative_to(base).as_posix() not in known:
                problems.append(f"orphan file assets/{f.relative_to(base).as_posix()}")
    return problems


def _resolve(c, doc: Document, doc_dir: Path) -> dict:
    if not isinstance(c, ImageComponent):
        return c.model_dump()
    a = doc.assets[c.asset_id]
    f = Path(doc_dir) / a.path
    if not f.is_file() or _sha(f) != a.sha256:
        raise FileNotFoundError(f"{c.asset_id}: {a.path} missing or modified")
    return {"type": "image", "asset_id": c.asset_id, "file": str(f), "width": a.width, "height": a.height,
            "certain": a.certain, "description": a.description}


def reconstruct(doc: Document, qid: str, doc_dir: Path) -> dict:
    """One question, fully resolved, in its stored order. Deterministic: same input -> identical output."""
    q = next((x for x in doc.questions if x.qid == qid), None)
    if q is None:
        raise KeyError(qid)
    return {"qid": q.qid, "number": q.number, "section": q.section, "pages": q.source.pages,
            "stem": [_resolve(c, doc, doc_dir) for c in q.stem],
            "options": [{"label": o.label, "content": [_resolve(c, doc, doc_dir) for c in o.content]}
                        for o in q.options],
            "answer": q.answer.label if q.answer else None, "flags": q.flags}


def to_markdown(rq: dict) -> str:
    """Human-readable rendering of a reconstructed question (images as relative markdown links)."""
    def render(cs):
        parts = []
        for c in cs:
            if c["type"] == "text":
                parts.append(c["text"])
            elif c["type"] == "equation":
                parts.append(f"$${c['latex']}$$")
            elif c["type"] == "table":
                parts.append(c["html"])
            else:
                parts.append(f"![{c['asset_id']}]({Path(c['file']).as_posix()})")
        return "\n".join(parts)
    head = f"**{rq['number']}.** " if rq["number"] else ""
    out = [head + render(rq["stem"])]
    for o in rq["options"]:
        out.append(f"({o['label']}) " + render(o["content"]))
    if rq["answer"]:
        out.append(f"Answer: {rq['answer']}")
    return "\n\n".join(out)
