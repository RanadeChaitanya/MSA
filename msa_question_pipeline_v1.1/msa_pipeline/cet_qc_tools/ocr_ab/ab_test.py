"""A/B: queue OCR (120 dpi render) vs 300 dpi render (uses the scan's ~300 dpi text layer). Scores both against human-checked v7.
python ab_test.py score            -> score whatever exists
python ab_test.py ocr 1 2 4        -> OCR those pages at 300 dpi, then score
"""
import base64, difflib, json, re, sys, time
from pathlib import Path
import fitz, httpx

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); AB = R / "ocr_ab"
PDF = r"D:\MyDataset\CET PCB 2024 (2).pdf"
OPTS = [dict(temperature=0, repeat_penalty=1.1, num_predict=6000), dict(temperature=0, repeat_penalty=1.25, num_predict=4096)]


def ocr(page, dpi=300):
    out = AB / f"page_{page:03d}_{dpi}.txt"
    if out.exists():
        return
    png = AB / f"page_{page:03d}_{dpi}.png"
    fitz.open(PDF)[page - 1].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).save(png)
    img = base64.b64encode(png.read_bytes()).decode()
    t = time.time()
    for o in OPTS:
        r = httpx.post("http://localhost:11434/api/generate", timeout=3600, json=dict(
            model="deepseek-ocr", prompt="<|grounding|>Convert the document to markdown.", stream=False, images=[img], options=o)).json()
        if r.get("done_reason") != "length" and len(r["response"]) < 9000:
            break
    out.write_text(r["response"], encoding="utf-8")
    print(f"page {page} @ {dpi} dpi: {time.time() - t:.0f}s", flush=True)


def norm(s):
    s = re.sub(r"<\|ref\|>.*?<\|/ref\|><\|det\|>.*?<\|/det\|>", " ", s, flags=re.S)  # grounding tags
    s = re.sub(r"\\(mathrm|text|left|right|mathbf|operatorname|displaystyle|quad|,|;|!)", "", s)
    return re.sub(r"[\s{}$]+", "", s).lower()


def score():
    v7 = json.load(open(R / "final" / "paper_01_v7.json", encoding="utf-8"))
    qs = v7["questions"] if isinstance(v7, dict) else v7
    rows = []
    for f in sorted(AB.glob("page_*_300.txt")):
        p = int(f.name[5:8])
        gold = [x for x in qs if str(x.get("page")) == str(p)]
        g = norm(" ".join(x["stem"] + " " + " ".join(str((o or {}).get("text", "") if isinstance(o, dict) else o)
                                                   for o in (x.get("options") or {}).values()) for x in gold))
        a = norm((R / "ocr" / f"page_{p:03d}.txt").read_text(encoding="utf-8"))
        b = norm(f.read_text(encoding="utf-8"))
        # share of the human-checked text recovered, in order (matching blocks / gold length)
        rec = lambda o: sum(m.size for m in difflib.SequenceMatcher(None, g, o, autojunk=False).get_matching_blocks()) / max(len(g), 1)
        # per-question exact hits: normalized stem fully present in the OCR
        hit = lambda o: sum(norm(x["stem"])[:60] in o for x in gold)
        rows.append(dict(page=p, questions=len(gold), recall_120=round(rec(a), 4), recall_300=round(rec(b), 4),
                         stem_hits_120=hit(a), stem_hits_300=hit(b)))
    (AB / "ab_result.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r)


if __name__ == "__main__":
    if sys.argv[1] == "ocr":
        for p in map(int, sys.argv[2:]):
            ocr(p)
    score()
