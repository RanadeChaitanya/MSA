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
    vf = sub.add_parser("verify", help="check document.json against its assets (paths, sha256, owners, orphans)")
    vf.add_argument("doc_dir")
    sh = sub.add_parser("show", help="rebuild one question from document.json and print it as markdown")
    sh.add_argument("doc_dir")
    sh.add_argument("qid")
    sub.add_parser("schema", help="print the JSON Schema of document.json")
    args = ap.parse_args(argv)
    if args.cmd == "schema":
        from .schema import json_schema
        print(json.dumps(json_schema(), indent=2))
        return 0
    if args.cmd in ("verify", "show"):
        from .reconstruct import load_document, reconstruct, to_markdown, verify
        doc = load_document(Path(args.doc_dir))
        if args.cmd == "show":
            print(to_markdown(reconstruct(doc, args.qid, Path(args.doc_dir))))
            return 0
        problems = verify(doc, Path(args.doc_dir))
        print("\n".join(problems) if problems else f"OK: {len(doc.questions)} questions, {len(doc.assets)} assets")
        return 1 if problems else 0
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
