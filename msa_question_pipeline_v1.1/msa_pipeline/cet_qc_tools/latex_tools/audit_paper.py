"""Data-integrity audit of a paper's newest stage file and its built deliverables. Read-only.
python audit_paper.py P"""
import json, re, sys, zipfile
from collections import Counter
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); P = int(sys.argv[1]); PP = f"{P:02d}"
CP = Path(r"D:\MSA\msa_question_pipeline_v1.1\msa_pipeline\.claude\worktrees\distracted-sanderson-cae518\msa_question_pipeline_v1.1\msa_pipeline")
sys.path[:0] = [str(CP / "claude_pipeline"), str(CP)]
stage = sorted((R / "final").glob(f"paper_{PP}_v*.json"), key=lambda f: int(re.search(r"_v(\d+)", f.name)[1]))[-1]
S = json.load(open(stage, encoding="utf-8"))["questions"]
issues = []
add = lambda q, what: issues.append((q.split("_P" + PP + "_")[-1] if isinstance(q, str) else q, what))

# --- stage file
ids = [q["qid"] for q in S]
for q, c in Counter(ids).items():
    if c > 1: add(q, f"duplicate qid x{c}")
want = {"Physics": 50, "Chemistry": 50, "Biology": 100}
for subj, n in want.items():
    have = sorted(int(q["number"]) for q in S if q["subject"] == subj)
    miss = sorted(set(range(1, n + 1)) - set(have))
    if miss or len(have) != n: add(subj, f"numbering: {len(have)} questions, missing {miss}")
TAG = re.compile(r"<\|ref\||<\|det\||\w+\[\[\d|</?t[dr]>|\\\(|\\\)|\\\[|\\\]")
for q in S:
    k = q["qid"]
    if q.get("correct_option") not in tuple("ABCD"): add(k, f"bad key {q.get('correct_option')!r}")
    if set((q.get("options") or {}).keys()) != set("ABCD"): add(k, f"option keys {sorted((q.get('options') or {}).keys())}")
    try:
        if not 1 <= int(q["page"]) <= 428: add(k, f"page {q['page']}")
    except Exception: add(k, f"page not int {q.get('page')!r}")
    b = q.get("bbox")
    if b and (len(b) != 4 or not all(0 <= v <= 999 for v in b) or b[0] >= b[2] or b[1] >= b[3]): add(k, f"bbox {b}")
    fields = [("stem", q["stem"])] + [(f"opt{L}", (q["options"] or {}).get(L)) for L in "ABCD"]
    for f, t in fields:
        if t is None: continue
        if not isinstance(t, str): add(k, f"{f} not text"); continue
        if TAG.search(t): add(k, f"{f}: OCR/HTML artefact {TAG.search(t).group(0)!r}")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", t): add(k, f"{f}: control character")
        if t != t.strip(): add(k, f"{f}: leading/trailing space")
        if "  " in t: add(k, f"{f}: double space")
        if t.count("$") % 2: add(k, f"{f}: odd $")
        odd = set(re.findall(r"[^\x00-\x7f]", t)) - set("°±×÷–—’‘“”…µΩαβγδθλμπρσφωΔ√∞≤≥→⇌")
        if odd: add(k, f"{f}: unusual unicode {odd}")
    if "[figure]" in q["stem"] and not (q.get("figures") or {}).get("question"): add(k, "stem says [figure] but no figure box")
    if (q.get("figures") or {}).get("question") and "[figure]" not in q["stem"]: add(k, "figure box but no [figure] marker in stem")
    for L in "ABCD":
        img = (q.get("option_images") or {}).get(L)
        if img and not (R / img).exists(): add(k, f"option image missing {img}")
    if q.get("release") is False and not q.get("v11_note"): add(k, "held back without a reason")

# --- deliverables
for kind in ("original", "regenerated"):
    f = R / "papers" / f"paper_{PP}_{kind}.json"
    if not f.exists(): add(kind, "deliverable missing"); continue
    d = json.load(open(f, encoding="utf-8"))
    if d["run"].get("stage") != re.search(r"_(v\d+)", stage.name)[1]: add(kind, f"deliverable built from {d['run'].get('stage')}, newest is {stage.name}")
    try:
        from cet_tools import CetDocument
        CetDocument(**d)
    except Exception as e:
        add(kind, f"schema: {str(e).splitlines()[0][:120]}")
    Dq = {q["qid"]: q for q in d["questions"]}
    if set(Dq) != set(ids): add(kind, f"question set differs: {len(Dq)} vs {len(ids)}")
    for q in S:
        x = Dq.get(q["qid"])
        if not x: continue
        if x["text"] != q["stem"] or x["correct_option"] != q["correct_option"]: add(q["qid"], f"{kind}: content differs from stage")
        if q.get("release") is False and x.get("release") is not False: add(q["qid"], f"{kind}: release flag lost (run enrich_deliverable.py)")
        figs = (q.get("figures") or {})
        if figs.get("question") and not x["question_images"]: add(q["qid"], f"{kind}: question figure lost")
        for L in "ABCD":
            if figs.get(L) and not (x["options"][L].get("image")): add(q["qid"], f"{kind}: option {L} image lost")
    z = R / "papers" / f"paper_{PP}_images_{kind}.zip"
    names = {n.split("/")[-1] for n in zipfile.ZipFile(z).namelist()} if z.exists() else set()
    refs = [i["path"] for x in d["questions"] for i in x["question_images"]] + [x["options"][L]["image"]["path"] for x in d["questions"] for L in "ABCD" if x["options"][L].get("image")]
    lost = [r for r in refs if Path(r).name not in names]
    if lost: add(kind, f"{len(lost)} image refs not in zip, e.g. {lost[:2]}")
    if len(names) != len(set(Path(r).name for r in refs)): add(kind, f"zip has {len(names)} files, JSON references {len(set(Path(r).name for r in refs))}")

print(f"audit of {stage.name} + deliverables: {len(issues)} issue(s)")
for c, n in Counter(w.split(':')[0] if ':' in w else w for _, w in issues).most_common(): print(f"  {n:3d}  {c}")
for q, w in issues[:60]: print("   ", q, "|", w[:140])
