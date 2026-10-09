"""Score pipeline output against tests/ground_truth/<id>.json (see build_ground_truth.py for the GT format).

Metrics (each reported with its sample size):
  boundary     recall = GT questions found (page+number, or page+order when unnumbered); precision = found / emitted
  options      complete GT questions whose labels A-D are all present and non-empty (text or image)
  option_text  GT option texts matched after normalisation (similarity >= 0.85)
  images       picture slots (stem, option A..D): TP/FP/FN of picture counts -> precision / recall
  association  emitted pictures that sit in a slot where GT has a picture (foreign pictures count as wrong)
  reconstruct  v2 only: document verifies, every found question rebuilds with all its images resolved
Reads v2 (document.json) and the legacy v1 (output/questions.json) format, so a baseline can be compared.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

_LATEX = re.compile(r"\\(?:mathrm|text|mathbf|operatorname)\{([^}]*)\}")
_TAGS = re.compile(r"<[^>]+>")


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "")
    t = _LATEX.sub(r"\1", _TAGS.sub("", t))
    t = t.replace("\\times", "x").replace("×", "x").replace("·", ".").replace("\\cdot", ".").replace("∧", "λ")
    t = t.replace("Λ", "λ").replace("\\wedge", "λ").replace("\\vee", "v").replace("∨", "v").replace("\\sqrt", "√")
    t = t.replace("\\lor", "v").replace("\\land", "λ").replace("\\neg", "~").replace("\\sim", "~")
    t = re.sub(r"\\[a-zA-Z]+", "", t)
    return re.sub(r"[\s${}^_,]+", "", t).lower()


def _load_pred(doc_dir: Path) -> tuple[list[dict], str, list[str]]:
    v2 = doc_dir / "document.json"
    if v2.exists():
        from pipeline.reconstruct import load_document, reconstruct, verify
        doc = load_document(doc_dir)
        problems = verify(doc, doc_dir)
        out = []
        for q in doc.questions:
            try:
                rq = reconstruct(doc, q.qid, doc_dir)
                ok = True
            except Exception:
                rq, ok = None, False

            FALLBACK = ("option_region", "ink_fallback", "math_text_crop")

            def split(cs):
                return (" ".join(c.text if c.type == "text" else getattr(c, "latex", "") for c in cs
                                 if c.type in ("text", "equation")),
                        sum(1 for c in cs if c.type == "image"
                            and doc.assets[c.asset_id].method not in ("table_crop",) + FALLBACK))

            def fallbacks(cs):
                return sum(1 for c in cs if c.type == "image" and doc.assets[c.asset_id].method in FALLBACK)
            st, si = split(q.stem)
            tables = sum(1 for c in q.stem if c.type == "image" and doc.assets[c.asset_id].method == "table_crop")
            opts = {o.label: dict(zip(("text", "images"), split(o.content)), fallback=fallbacks(o.content))
                    for o in q.options}
            out.append({"page": q.source.pages[0], "number": q.number, "y": (q.source.bbox or [0, 9, 0, 9])[1],
                        "stem_text": st, "stem_images": si, "tables": tables, "options": opts, "rebuilt": ok})
        return out, "v2", problems
    legacy = json.loads((doc_dir / "output" / "questions.json").read_text(encoding="utf-8"))
    out = []
    for q in legacy["questions"]:
        opts = {L: {"text": o.get("text") or "", "images": 1 if o.get("image") else 0, "fallback": 0}
                for L, o in q["options"].items()}
        out.append({"page": q["page"], "number": q.get("number") or None, "y": (q.get("bbox_px") or [0, 9e9])[1],
                    "stem_text": q.get("text") or "", "stem_images": len(q.get("question_images") or []), "tables": 0,
                    "options": opts, "rebuilt": None})
    return out, "v1", []


def score_sample(doc_dir: Path, gt: dict) -> dict:
    preds, fmt, problems = _load_pred(doc_dir)
    by_page: dict[int, list[dict]] = {}
    for p in preds:
        by_page.setdefault(p["page"], []).append(p)
    for lst in by_page.values():
        lst.sort(key=lambda p: p["y"])
    used: set[int] = set()
    m = {k: 0 for k in ("gt", "found", "opt_q", "opt_ok", "txt_n", "txt_ok", "tp", "fp", "fn", "img_emitted",
                        "img_right_slot", "rebuild_n", "rebuild_ok", "tab_gt", "tab_ok", "tab_fp", "fallback_crops")}
    fails: list[str] = []
    m["gt"] = len(gt["questions"])
    for g in gt["questions"]:
        cands = by_page.get(g["page"], [])
        if g["number"] is not None:
            hit = next((p for p in cands if p["number"] == g["number"] and id(p) not in used), None)
        else:
            unn = [p for p in cands if id(p) not in used]
            hit = unn[0] if unn else None
        tag = f"p{g['page']} Q{g['number'] if g['number'] is not None else '#' + str(g['order'])}"
        if hit is None:
            fails.append(f"{tag}: not found")
            continue
        used.add(id(hit))
        m["found"] += 1
        if g["complete"]:
            m["opt_q"] += 1
            missing = [L for L in g["options"]
                       if L not in hit["options"] or not (hit["options"][L]["text"].strip() or hit["options"][L]["images"]
                                                          or hit["options"][L].get("fallback"))]
            m["fallback_crops"] += sum(o.get("fallback", 0) for o in hit["options"].values())
            if not missing:
                m["opt_ok"] += 1
            else:
                fails.append(f"{tag}: options missing/empty {missing}")
            for L, t in (g.get("texts") or {}).items():
                m["txt_n"] += 1
                got = hit["options"].get(L, {}).get("text", "")
                if SequenceMatcher(None, norm(t), norm(got)).ratio() >= 0.85:
                    m["txt_ok"] += 1
                else:
                    fails.append(f"{tag}: option {L} text {got[:40]!r} != {t!r}")
            if fmt == "v2":
                m["rebuild_n"] += 1
                if hit["rebuilt"] and not missing:
                    m["rebuild_ok"] += 1
        if g.get("table") and not hit["tables"] and hit["stem_images"] > g["stem_images"]:
            hit = dict(hit, tables=1, stem_images=hit["stem_images"] - 1)   # table printed as a picture
        if g.get("table"):
            m["tab_gt"] += 1
            m["tab_ok"] += 1 if hit["tables"] >= 1 else 0
            if not hit["tables"]:
                fails.append(f"{tag}: table not saved as an image")
        m["tab_fp"] += max(0, hit["tables"] - (1 if g.get("table") else 0))
        if hit["tables"] and not g.get("table"):
            fails.append(f"{tag}: {hit['tables']} table image(s) but no table in ground truth")
        slots = [("stem", g["stem_images"], hit["stem_images"])]
        slots += [(L, g["opt_images"].get(L, 0), hit["options"].get(L, {}).get("images", 0)) for L in "ABCD"]
        for slot, want, got in slots:
            m["tp"] += min(want, got)
            m["fp"] += max(0, got - want)
            m["fn"] += max(0, want - got)
            m["img_emitted"] += got
            m["img_right_slot"] += min(want, got)
            if want != got:
                fails.append(f"{tag}: {slot} pictures {got} (expected {want})")
    extra = [p for p in preds if id(p) not in used]
    for p in extra:
        fails.append(f"p{p['page']} Q{p['number']}: emitted but not in ground truth"
                     + (f" (+{p['stem_images'] + sum(o['images'] for o in p['options'].values())} pictures)"))
        imgs = p["stem_images"] + sum(o["images"] for o in p["options"].values())
        m["fp"] += imgs
        m["img_emitted"] += imgs
    return {"format": fmt, "emitted": len(preds), "metrics": m, "verify_problems": problems, "failures": fails}


def _pct(a, b):
    return None if b == 0 else round(100.0 * a / b, 1)


def score_all(out_dir: Path, gt_dir: Path) -> dict:
    per, tot = {}, {}
    for gtf in sorted(gt_dir.glob("*.json")):
        if gtf.name == "samples.json":
            continue
        gt = json.loads(gtf.read_text(encoding="utf-8"))
        d = Path(out_dir) / gt["id"]
        if not ((d / "document.json").exists() or (d / "output" / "questions.json").exists()):
            per[gt["id"]] = {"error": "no output"}
            continue
        r = score_sample(d, gt)
        per[gt["id"]] = r
        for k, v in r["metrics"].items():
            tot[k] = tot.get(k, 0) + v
        tot["emitted"] = tot.get("emitted", 0) + r["emitted"]
        tot["verify_problems"] = tot.get("verify_problems", 0) + len(r["verify_problems"])
    t = tot
    summary = {
        "boundary_recall_%": _pct(t.get("found", 0), t.get("gt", 0)), "n_gt_questions": t.get("gt", 0),
        "boundary_precision_%": _pct(t.get("found", 0), t.get("emitted", 0)), "n_emitted": t.get("emitted", 0),
        "options_complete_%": _pct(t.get("opt_ok", 0), t.get("opt_q", 0)), "n_complete_questions": t.get("opt_q", 0),
        "option_text_%": _pct(t.get("txt_ok", 0), t.get("txt_n", 0)), "n_option_texts": t.get("txt_n", 0),
        "image_precision_%": _pct(t.get("tp", 0), t.get("tp", 0) + t.get("fp", 0)),
        "image_recall_%": _pct(t.get("tp", 0), t.get("tp", 0) + t.get("fn", 0)),
        "n_gt_images": t.get("tp", 0) + t.get("fn", 0), "n_emitted_images": t.get("img_emitted", 0),
        "association_%": _pct(t.get("img_right_slot", 0), t.get("img_emitted", 0)),
        "reconstruct_%": _pct(t.get("rebuild_ok", 0), t.get("rebuild_n", 0)),
        "table_recall_%": _pct(t.get("tab_ok", 0), t.get("tab_gt", 0)), "n_gt_tables": t.get("tab_gt", 0),
        "table_false_images": t.get("tab_fp", 0),
        "fallback_cell_crops": t.get("fallback_crops", 0),
        "verify_problems": t.get("verify_problems", 0),
        "missing_outputs": [k for k, v in per.items() if "error" in v],
    }
    return {"summary": summary, "per_sample": per}
