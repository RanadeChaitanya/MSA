"""Run the pipeline on the ground-truth samples and score the output.

    python scripts/bench.py run   --out <dir> [--env .env] [--only id,id] [--offline]
    python scripts/bench.py score --out <dir>

`run` writes <out>/<sample id>/... (one pipeline doc per sample); `score` compares every
<out>/<id>/output/questions.json with tests/ground_truth/<id>.json and prints per-metric accuracy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.config import Settings                 # noqa: E402
from pipeline.utils.log import setup                  # noqa: E402

GT = ROOT / "tests" / "ground_truth"
PDF_ROOTS = {"testers": ROOT / "testers", "downloads": Path.home() / "Downloads"}


def resolve_pdf(spec: str, roots: dict) -> Path:
    root, name = spec.split(":", 1)
    return Path(roots[root]) / name


def cmd_run(a) -> int:
    from pipeline.lat.client import LatClient, LatError
    from pipeline.runner import process_document
    cfg = Settings.load(a.env)
    setup(cfg.log_level)
    roots = dict(PDF_ROOTS)
    if a.testers:
        roots["testers"] = Path(a.testers)
    samples = json.loads((GT / "samples.json").read_text(encoding="utf-8"))["samples"]
    only = set(a.only.split(",")) if a.only else None
    out = Path(a.out)
    cfg.data_dir = out
    report = {}
    for s in samples:
        if only and s["id"] not in only:
            continue
        pdf = resolve_pdf(s["pdf"], roots)
        if not pdf.exists():
            print(f"[{s['id']}] MISSING PDF: {pdf}")              # never skip silently
            report[s["id"]] = {"error": f"missing pdf {pdf}"}
            continue
        client = LatClient(cfg)
        if a.offline:
            def _post(path, **kw):
                raise LatError(f"offline: cache miss on {path}")
            client._post = _post                                 # type: ignore[assignment]
        t0 = time.time()
        try:
            doc = process_document(pdf, cfg, client=client, doc_id=s["id"], pages=s["pages"])
            report[s["id"]] = {"questions": len(doc.questions), "run": doc.run, "seconds": round(time.time() - t0, 1)}
        except Exception as e:                                   # report, keep going with the next sample
            report[s["id"]] = {"error": repr(e)[:300]}
        print(f"[{s['id']}] {report[s['id']]}")
    (out / "bench_run.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


def cmd_score(a) -> int:
    from tests.scoring import score_all
    res = score_all(Path(a.out), GT)
    print(json.dumps(res["summary"], indent=2))
    (Path(a.out) / "bench_score.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--env", default=".env")
    r.add_argument("--only")
    r.add_argument("--testers")
    r.add_argument("--offline", action="store_true")
    s = sub.add_parser("score")
    s.add_argument("--out", required=True)
    a = ap.parse_args()
    return cmd_run(a) if a.cmd == "run" else cmd_score(a)


if __name__ == "__main__":
    raise SystemExit(main())
