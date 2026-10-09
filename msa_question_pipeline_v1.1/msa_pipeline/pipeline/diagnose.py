"""`python -m pipeline.cli diagnose <doc_id>`  — explains WHY pages produced few questions, from lat_raw/*.json.
Prints per-page block-type counts, whether atomic (line) grounding exists, and opaque-table counts."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .stages.structure import iter_nodes


def diagnose(doc_dir: Path, limit_pages: int = 80) -> str:
    lines = []
    totals = Counter()
    for f in sorted((doc_dir / "lat_raw").glob("parse_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        lines.append(f"\n== {f.name}  (markdown chars: {len(d.get('markdown', ''))})")
        for pnode in (d.get("structure") or {}).get("children", [])[:limit_pages]:
            page = pnode["grounding"]["page"]
            types, atomic, tables_no_children, tables = Counter(), 0, 0, 0
            stack = list(pnode.get("children") or [])
            while stack:
                el = stack.pop()
                types[el["type"]] += 1
                atomic += len(el.get("atomic_grounding") or [])
                if el["type"] == "table":
                    tables += 1
                    if not el.get("children"):
                        tables_no_children += 1
                stack.extend(el.get("children") or [])
            totals.update(types)
            lines.append(f"  page {page:>3}: {dict(types)} | atomic_lines={atomic} | tables={tables} "
                         f"(opaque, no cell children: {tables_no_children})")
    lines.append(f"\nTOTAL block types: {dict(totals)}")
    ex = sorted((doc_dir / "lat_raw").glob("parse_*.json"))
    if ex:
        lines.append(f"Share one raw file, e.g. {ex[0].name}, to tune layout rules.")
    return "\n".join(lines)
