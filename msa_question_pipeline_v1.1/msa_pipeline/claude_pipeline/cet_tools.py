"""Helpers for the CET PCB 2024 chat pipeline (agents call `view`/`check`; the coordinator calls `status`/`build`).
  python claude_pipeline/cet_tools.py view  PAGE x0,y0,x1,y1 OUT.png [DPI]   # box in permille (0-1000) of the page
  python claude_pipeline/cet_tools.py check P                                # validate raw question/answer JSON of paper P
  python claude_pipeline/cet_tools.py status                                 # progress bar
  python claude_pipeline/cet_tools.py build                                  # crops + DocumentOut for every paper with raw files
"""
import json, shutil, sys, time, zipfile
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path.home() / ".claude/skills/pdf-extract"))
from pipeline.models import AssetRef, DocumentOut, OptionOut, QuestionOut  # noqa: E402
import pdfkit  # noqa: E402

PDF = r"D:\MyDataset\CET PCB 2024 (2).pdf"
OUT = Path(r"D:\MSA\outputs\cet_pcb_2024")
DOC = "CET_PCB_2024"
# (paper no, exam date, shift, first question page, last question page)
PAPERS = [(1, "2024-04-22", "I", 1, 16), (2, "2024-04-22", "II", 17, 32), (3, "2024-04-23", "I", 33, 49),
          (4, "2024-04-23", "II", 50, 64), (5, "2024-04-24", "I", 65, 78), (6, "2024-04-24", "II", 79, 94),
          (7, "2024-04-27", "I", 95, 110), (8, "2024-04-27", "II", 111, 125), (9, "2024-04-28", "I", 126, 142),
          (10, "2024-04-28", "II", 143, 157), (11, "2024-04-29", "I", 158, 173), (12, "2024-04-29", "II", 174, 188),
          (13, "2024-04-30", "I", 189, 204), (14, "2024-04-30", "II", 205, 219)]
EXPECT = {"Physics": 50, "Chemistry": 50, "Biology": 100}
SUBJ = {"Physics": "Phy", "Chemistry": "Che", "Biology": "Bio"}


class CetQuestion(QuestionOut):
    """QuestionOut + exam fields. difficulty/topic/subtopic stay blank by decision; chapter_tag is the printed 'Std.12 | Ch-3'."""
    paper: int = 0
    exam_date: str = ""
    shift: str = ""
    subject: str = ""
    difficulty: str = ""
    topic: str = ""
    subtopic: str = ""
    chapter_tag: str = ""


class CetDocument(DocumentOut):
    questions: list[CetQuestion]


def raw(kind, p):
    return OUT / "raw" / f"paper_{p:02d}_{kind}.json"


def load(kind, p):
    f = raw(kind, p)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def view(page, box, out, dpi=200):
    pg = fitz.open(PDF)[page - 1]; r = pg.rect
    x0, y0, x1, y1 = [float(v) for v in box.split(",")]
    clip = fitz.Rect(x0 / 1000 * r.width, y0 / 1000 * r.height, x1 / 1000 * r.width, y1 / 1000 * r.height)
    pg.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY).save(out)


def check(p):
    """Code-only sanity checks. Returns list of (subject, number, reason)."""
    bad, q, a = [], load("questions", p), load("answers", p)
    if q:
        seen = {}
        for x in q["questions"]:
            seen.setdefault(x["subject"], []).append(x["number"])
            o, t = x["options"], x["text"].strip()
            figs = {f["belongs_to"] for f in x["figures"]}
            if not t and "question" not in figs: bad.append((x["subject"], x["number"], "empty text"))
            miss = [L for L in "ABCD" if not o.get(L, "").strip() and L not in figs]
            if miss: bad.append((x["subject"], x["number"], f"options missing {miss}"))
            vals = [o.get(L, "").strip() for L in "ABCD" if o.get(L, "").strip()]
            if len(set(vals)) < len(vals): bad.append((x["subject"], x["number"], "duplicate options"))
            if x.get("confidence", 1) < 0.85 or x.get("flags"): bad.append((x["subject"], x["number"], f"agent flag {x.get('flags')} conf {x.get('confidence')}"))
            for f in x["figures"]:
                b = f["box"]
                if not (0 <= b[0] < b[2] <= 1000 and 0 <= b[1] < b[3] <= 1000 and (b[2] - b[0]) > 15 and (b[3] - b[1]) > 10):
                    bad.append((x["subject"], x["number"], f"bad figure box {b}"))
        for s, n in EXPECT.items():
            got = sorted(seen.get(s, []))
            if got != list(range(1, n + 1)):
                bad.append((s, 0, f"numbers != 1..{n}: missing {sorted(set(range(1, n + 1)) - set(got))} dup/extra {sorted(set(x for x in got if got.count(x) > 1 or x > n))}"))
    if a:
        for s, n in EXPECT.items():
            got = a["answers"].get(s, {})
            miss = [i for i in range(1, n + 1) if str(i) not in got]
            if miss: bad.append((s, 0, f"answers missing {miss}"))
            bad += [(s, int(k), f"answer {v!r} not A-D/*") for k, v in got.items() if v not in ("A", "B", "C", "D", "*")]
    return bad


def merge(p):
    """Rebuild paper_PP_questions.json from raw/parts if an agent was cut off before writing the full file."""
    qs = {}
    for f in sorted((OUT / "raw" / "parts").glob(f"paper_{p:02d}_p*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for x in (d["questions"] if isinstance(d, dict) else d): qs[(x["subject"], x["number"])] = x
    pg = [q["page"] for q in qs.values()]
    raw("questions", p).write_text(json.dumps(dict(paper=p, pages=[min(pg), max(pg)], questions=list(qs.values()), notes="merged from parts"), ensure_ascii=False, indent=1), encoding="utf-8")
    print(p, len(qs), "questions merged")


def status():
    done = lambda k, p: raw(k, p).exists()
    stages = [("questions read", lambda p: done("questions", p)), ("answers read", lambda p: done("answers", p)),
              ("checked clean", lambda p: done("questions", p) and done("answers", p) and not check(p)),
              ("built", lambda p: (OUT / "papers" / f"paper_{p:02d}_original.json").exists())]
    tot = len(PAPERS) * len(stages); n = sum(f(p[0]) for _, f in stages for p in PAPERS)
    print(f"[{'#' * (40 * n // tot):<40}] {100 * n // tot}%  ({n}/{tot} steps)")
    for name, f in stages: print(f"  {name:<15} {sum(f(p[0]) for p in PAPERS):>2}/{len(PAPERS)}")


def build():
    doc = fitz.open(PDF)
    (OUT / "papers").mkdir(exist_ok=True)
    allq = {"original": [], "regenerated": []}
    for p, date, shift, p0, p1 in PAPERS:
        q, a = load("questions", p), load("answers", p)
        if not (q and a): continue
        ans, chap = a["answers"], a.get("chapters", {})
        var = {"original": [], "regenerated": []}
        for x in q["questions"]:
            s, n = x["subject"], x["number"]
            nm = f"{DOC}_P{p:02d}_{SUBJ[s]}_q{n:03d}"
            qi, oi, flags = {"original": [], "regenerated": []}, {"original": {}, "regenerated": {}}, list(x.get("flags", []))
            for i, f in enumerate(x["figures"], 1):
                W, H = doc[f["page"] - 1].rect.width, doc[f["page"] - 1].rect.height
                b = f["box"]; box = [b[0] / 1000 * W, b[1] / 1000 * H, b[2] / 1000 * W, b[3] / 1000 * H]
                try:
                    o, r = pdfkit.crop_pair(doc, f["page"], box, f"{nm}_fig{i}.png", OUT)
                except Exception as e:
                    flags.append(f"crop_failed_{i}:{e}"[:80]); continue
                for v, ar in (("original", o), ("regenerated", r)):
                    ar = AssetRef(**ar)
                    if f["belongs_to"] in ("A", "B", "C", "D"): oi[v][f["belongs_to"]] = ar
                    else: qi[v].append(ar)
            co = ans.get(s, {}).get(str(n))
            for v in var:
                opts = {L: OptionOut(text=x["options"].get(L, ""), image=oi[v].get(L), image_only=not x["options"].get(L, "").strip() and L in oi[v]) for L in "ABCD"}
                var[v].append(CetQuestion(qid=nm, number=n, number_text=str(n), page=x["page"], column=x.get("column", 0), text=x["text"],
                              correct_option=None if co in (None, "*") else co, answer_text="", question_images=qi[v], options=opts,
                              confidence=x.get("confidence", 1.0), flags=flags + (["answer_marks_to_all"] if co == "*" else []) + ([] if co else ["no_answer"]),
                              paper=p, exam_date=date, shift=shift, subject=s, chapter_tag=chap.get(s, {}).get(str(n), "")))
        for v, qs in var.items():
            d = CetDocument(doc_id=f"{DOC}_P{p:02d}", source_pdf=PDF, page_count=p1 - p0 + 1, questions=qs,
                            run=dict(engine="claude_chat_pipeline", images=v, dpi=200, paper=p, exam_date=date, shift=shift, option_map="A-D"))
            (OUT / "papers" / f"paper_{p:02d}_{v}.json").write_text(d.model_dump_json(indent=1), encoding="utf-8")
            allq[v] += qs
    for v, qs in allq.items():
        if not qs: continue
        d = CetDocument(doc_id=DOC, source_pdf=PDF, page_count=len(doc), questions=qs, run=dict(engine="claude_chat_pipeline", images=v))
        (OUT / f"{DOC}_{v}.json").write_text(d.model_dump_json(indent=1), encoding="utf-8")
        with zipfile.ZipFile(OUT / f"{DOC}_images_{v}.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted((OUT / f"images_{v}").glob("*.png")): z.write(f, f"images/{f.name}")
    bk = OUT / "backup" / f"build_{time.strftime('%Y%m%d_%H%M%S')}"
    bk.mkdir(parents=True)
    for f in OUT.glob(f"{DOC}_*"): shutil.copy2(f, bk)
    shutil.copytree(OUT / "raw", bk / "raw")
    print("built", {v: len(q) for v, q in allq.items()}, "backup", bk.name)


if __name__ == "__main__":
    c, a = sys.argv[1], sys.argv[2:]
    if c == "view": view(int(a[0]), a[1], a[2], int(a[3]) if len(a) > 3 else 200)
    elif c == "check":
        r = check(int(a[0])); print(json.dumps(r, ensure_ascii=False) if r else "OK")
    elif c == "merge": merge(int(a[0]))
    elif c == "status": status()
    elif c == "build": build()
