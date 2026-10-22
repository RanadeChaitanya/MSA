"""Answer-page parser for the CET book ('N. (X) Std.12 | Ch-N' lines) and its evaluation. Code only.
parse_pages(ocr_dir, pages) -> {(subject, n): letter or '*'} plus anomalies. Subject from '# PHYSICS' headings, else from the number reset.
"""
import json, re
from pathlib import Path

LINE = re.compile(r"^\s*(\d{1,3})\s*\.\s*\(\s*(?:\\mathrm\{\s*)?([A-D*])\s*\}?\s*\)", re.M)
HEAD = re.compile(r"^#\s*(PHYSICS|CHEMISTRY|BIOLOGY)\b", re.M | re.I)
SUBJ = ["Physics", "Chemistry", "Biology"]


TAG = re.compile(r"\s*(Std\.?\s*\d+\s*\|\s*Ch-\s*\d+)")


def parse_pages(ocr_dir, pages, tags=None):
    """tags (optional dict) receives {(subject, n): 'Std.12 | Ch-2'}"""
    ans, anomalies, subj, prev = {}, [], 0, 0
    for pg in pages:
        f = Path(ocr_dir) / f"page_{pg:03d}.txt"
        if not f.exists():
            continue
        txt = f.read_text(encoding="utf-8")
        # walk headings and answer lines in file order
        ev = sorted([(m.start(), "H", m.group(1).title()) for m in HEAD.finditer(txt)] + [(m.start(), "A", (int(m.group(1)), m.group(2), m.end())) for m in LINE.finditer(txt)])
        for _, k, v in ev:
            if k == "H":
                subj = SUBJ.index(v); prev = 0
                continue
            n, L, end = v
            if n == 1 and prev > 5 and subj < 2 and not any(1 for _ in ()):
                subj += 1  # heading missing: numbering restarted
            key = (SUBJ[subj], n)
            if key in ans:
                anomalies.append(f"dup {key} p{pg}")
            ans[key] = L
            t = TAG.match(txt, end)
            if tags is not None and t:
                tags[key] = re.sub(r"\s+", " ", t.group(1))
            if n != prev + 1 and n != 1:
                anomalies.append(f"jump {SUBJ[subj][:3]} {prev}->{n} p{pg}")
            prev = n
    return ans, anomalies


def evaluate(ocr_dir, starts, base, adjud):
    out = dict(papers={})
    gt_f = Path(base) / "raw/paper_01_answers.json"
    gt = json.loads(gt_f.read_text(encoding="utf-8"))["answers"] if gt_f.exists() else {}
    pages1 = [p for p in range(220, 238) if (Path(ocr_dir) / f"page_{p:03d}.txt").exists()]
    if not pages1:
        return dict(status="not measurable: no answer pages OCR'd yet")
    ans, an = parse_pages(ocr_dir, pages1)
    cmp = dis = star = 0
    dis_l = []
    for (s, n), L in sorted(ans.items(), key=lambda x: (["Physics", "Chemistry", "Biology"].index(x[0][0]), x[0][1])):
        if L == "*":
            star += 1
        g = gt.get(s, {}).get(str(n))
        if g:
            cmp += 1
            if g != L:
                dis += 1; dis_l.append(f"{s[:3]}{n}: ocr {L} sonnet {g}")
    done = {x["qid"]: x for x in adjud if x.get("aspect") == "C"}
    return dict(paper1_pages=f"{pages1[0]}-{pages1[-1]}", parsed=len(ans), compared=cmp, agree=cmp - dis, rate=round((cmp - dis) / cmp, 4) if cmp else None,
                star=star, anomalies=an[:6], disagreements=dis_l, adjudicated=len(done))
