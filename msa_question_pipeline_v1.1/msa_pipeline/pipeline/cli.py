"""CLI:  python -m pipeline.cli run file.pdf [--pages 1-3] [--doc-id X] [--stop-after canonicalize]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

from .config import Settings
from .runner import parse_page_spec, process_document
from .utils.log import setup


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="process a PDF end to end")
    r.add_argument("pdf")
    r.add_argument("--pages", help="e.g. 1-3,5 (1-indexed)")
    r.add_argument("--doc-id")
    r.add_argument("--env", default=".env")
    r.add_argument("--stop-after", choices=["canonicalize"], help="inspect canonical page images without calling the API")
    dg = sub.add_parser("diagnose", help="explain block types/geometry found per page for an already-processed doc")
    dg.add_argument("doc_id")
    dg.add_argument("--env", default=".env")
    args = ap.parse_args(argv)
    cfg = Settings.load(args.env)
    if args.cmd == "diagnose":
        from .diagnose import diagnose
        print(diagnose(Path(cfg.data_dir) / args.doc_id))
        return 0
    setup(cfg.log_level)
    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"file not found: {pdf}", file=sys.stderr)
        return 2
    with pymupdf.open(str(pdf)) as d:
        n = d.page_count
    out = process_document(pdf, cfg, doc_id=args.doc_id, pages=parse_page_spec(args.pages, n),
                           stop_after=args.stop_after)
    print(json.dumps({"doc_id": out.doc_id, "pages": out.page_count, "questions": len(out.questions),
                      "run": out.run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
