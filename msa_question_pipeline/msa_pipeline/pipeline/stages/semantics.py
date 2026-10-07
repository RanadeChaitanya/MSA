"""STAGE 2 - Question semantics via Landing AI Extract, grounded CLIENT-SIDE.

Extract(markdown, schema) -> per-field {value, spans}; spans are [start,end) code-point offsets into the
markdown we sent. Every Parse block carries grounding.range (same coordinate system), so we overlap each
field's spans with the blocks ourselves (the officially documented alternative to /v2/ground; it is
free, needs no second HTTP call, and also works for ZDR organisations).
Each extracted question is bound to the geometric Region that holds most of its grounded block centres.
Fallback: match by printed question number if grounding is empty.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..lat.schemas import QUESTION_SCHEMA
from .parse_stage import BatchResult
from .segment import Region
from .structure import LEAF

log = logging.getLogger("pipeline.semantics")


@dataclass
class SemanticResult:
    items: list[dict] = field(default_factory=list)
    matched: dict[int, dict] = field(default_factory=dict)     # region.idx -> extracted item
    votes: dict[int, int] = field(default_factory=dict)
    unmatched_items: int = 0
    notes: list[str] = field(default_factory=list)


def _spans_of(leaf) -> list[tuple[int, int]]:
    """Accept both documented shapes: spans=[[s,e],...] and ranges=[{start,end},...]."""
    out: list[tuple[int, int]] = []
    if not isinstance(leaf, dict):
        return out
    for s in leaf.get("spans") or []:
        if isinstance(s, (list, tuple)) and len(s) == 2:
            out.append((int(s[0]), int(s[1])))
    for r in leaf.get("ranges") or []:
        if isinstance(r, dict) and "start" in r and "end" in r:
            out.append((int(r["start"]), int(r["end"])))
    return out


def run_semantics(client, br: BatchResult, regions: list[Region], sizes: dict[int, tuple[int, int]]) -> SemanticResult:
    res = SemanticResult()
    if not regions:
        return res
    try:
        ext = client.extract(br.markdown, QUESTION_SCHEMA)
    except Exception as e:
        res.notes.append(f"extract_failed:{str(e)[:100]}")
        log.error("Extract failed: %s", e)
        return res
    if ext.get("schema_violation_error"):
        res.notes.append("schema_violation:" + str(ext["schema_violation_error"])[:100])
    for w in ext.get("warnings") or []:
        res.notes.append("extract_warning:" + str(w)[:100])
    items = (ext.get("extraction") or {}).get("questions") or []
    meta_items = (ext.get("extraction_metadata") or {}).get("questions") or []
    res.items = items
    leaf_blocks = [b for b in br.blocks if b.type in LEAF and not b.watermark]

    taken: dict[int, tuple[int, dict]] = {}
    for i, item in enumerate(items):
        votes: dict[int, int] = {}
        meta = meta_items[i] if i < len(meta_items) and isinstance(meta_items[i], dict) else {}
        for _field, leaf in meta.items():
            for s, e in _spans_of(leaf):
                for b in leaf_blocks:
                    if b.rng[0] < e and b.rng[1] > s:
                        for r in regions:
                            if r.page == b.page and r.box.contains_point(b.box.cx, b.box.cy):
                                votes[r.idx] = votes.get(r.idx, 0) + 1
                                break
        if not votes:
            num = "".join(ch for ch in str(item.get("number", "")) if ch.isdigit())
            same = [r for r in regions if num and str(r.number) == num]
            if len(same) == 1:
                votes[same[0].idx] = 1
                res.notes.append(f"matched_by_number:{num}")
        if not votes:
            res.unmatched_items += 1
            continue
        ridx = max(votes, key=votes.get)
        if ridx not in taken or votes[ridx] > taken[ridx][0]:
            if ridx in taken:
                res.unmatched_items += 1
            taken[ridx] = (votes[ridx], item)
        else:
            res.unmatched_items += 1
    for ridx, (v, item) in taken.items():
        res.matched[ridx] = item
        res.votes[ridx] = v
    return res
