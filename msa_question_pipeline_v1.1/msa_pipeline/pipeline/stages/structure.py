"""Flatten Landing AI Gen2 `structure` into pixel-space Block objects (global page numbers)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..utils.geometry import Box

LEAF = {"text", "marginalia", "table_cell", "figure", "table"}   # tables: cropped as images, never anchors
TEXTLIKE = {"text", "marginalia", "table_cell"}

_COMMENT = re.compile(r"<!--.*?-->", re.S)
# answer-key grids ("Q. NO 1 2 3 / ANS A B C", "ANSWER KEY"): their numbers are not questions
KEY_TABLE = re.compile(r"answer\s*key|q\s*\.\s*no\b|\bans\b", re.I)


def key_table_ids(blocks: list) -> set:
    """Ids of answer-key tables (and of their cells' parents) among Parse blocks."""
    return {b.id for b in blocks if b.type == "table" and KEY_TABLE.search(b.plain or b.md or "")}
_ANCHOR = re.compile(r"<a id=['\"][^'\"]*['\"]></a>")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_md(md: str) -> str:
    return _ANCHOR.sub("", _COMMENT.sub("", md or "")).strip()


def plain(md: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", clean_md(md))).strip()


@dataclass
class Block:
    uid: str                    # unique across batches: "<batch>/<id>"
    id: str                     # raw Landing AI id (text-3, figure-0, table_cell-7 ...)
    type: str
    page: int                   # GLOBAL 1-indexed page
    local_page: int             # page index inside its parse batch (1-indexed)
    box: Box                    # pixel box on the canonical page image
    md: str = ""
    plain: str = ""
    rng: tuple[int, int] = (0, 0)
    parent: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    atomic: list[dict] = field(default_factory=list)   # [{"box": Box, "conf": float|None}]
    batch_id: int = 0
    watermark: bool = False


def flatten(structure: dict, markdown: str, page_nums: list[int], sizes: dict[int, tuple[int, int]],
            batch_id: int) -> list[Block]:
    out: list[Block] = []
    for pnode in structure.get("children") or []:
        if pnode.get("status", "ok") == "failed":
            continue
        local = pnode["grounding"]["page"]
        gpage = page_nums[local - 1]
        W, H = sizes[gpage]

        def walk(el: dict, parent: Optional[str]) -> None:
            g = el["grounding"]
            r = g.get("range") or {"start": 0, "end": 0}
            if isinstance(r, (list, tuple)):
                r = {"start": r[0], "end": r[1]}
            md = el.get("markdown")
            if md is None:
                md = markdown[r["start"]:r["end"]]
            atoms = []
            for a in el.get("atomic_grounding") or []:
                if not a.get("box"):
                    continue
                ar = a.get("range")
                if isinstance(ar, dict):
                    ar = (ar.get("start"), ar.get("end"))
                elif isinstance(ar, (list, tuple)) and len(ar) == 2:
                    ar = (ar[0], ar[1])
                else:
                    ar = None
                atoms.append({"box": Box.from_norm(a["box"], W, H), "conf": a.get("confidence"), "rng": ar})
            b = Block(uid=f"{batch_id}/{el['id']}", id=el["id"], type=el["type"], page=gpage, local_page=local,
                      box=Box.from_norm(g["box"], W, H), md=clean_md(md), plain=plain(md),
                      rng=(r["start"], r["end"]), parent=parent, row=el.get("row"), col=el.get("col"),
                      atomic=atoms, batch_id=batch_id)
            if b.type == "figure":
                b.plain = ""
            out.append(b)
            for ch in el.get("children") or []:
                walk(ch, el["id"])

        for el in pnode.get("children") or []:
            walk(el, None)
    return out


def mark_watermarks(blocks: list[Block], cfg: Settings, W: int, H: int) -> None:
    pats = [re.compile(p, re.I) for p in cfg.watermark_patterns]
    for b in blocks:
        # Only SHORT blocks can be a watermark. A page-sized block of real question text that merely contains
        # the watermark phrase (or is simply big) must stay usable, otherwise whole pages lose their geometry.
        if b.type in ("text", "marginalia") and b.plain and len(b.plain) <= 80:
            if any(p.search(b.plain) for p in pats) or b.box.area > 0.25 * W * H:
                b.watermark = True


def iter_nodes(structure: dict):
    for p in structure.get("children") or []:
        for el in p.get("children") or []:
            yield from _iter(el)


def _iter(el: dict):
    yield el
    for ch in el.get("children") or []:
        yield from _iter(ch)
