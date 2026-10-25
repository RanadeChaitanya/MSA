"""One command for every code-only QC layer of a paper. Input = newest stage file; output = next version (final/paper_PP_vN.json).
  python cet_qc.py P            run all layers, write qc/paper_PP/ reports + the review PDF
  python cet_qc.py P --no-pdf   skip the PDF
Layers: 1 answer key (3-way) -> 2 LaTeX rules + canonical form -> 3 strict KaTeX gate -> 4 units/duplicates ->
        5 text vs book text files (numbers, symbols, words) -> 6 crops + model worklist -> 7 review PDF + gold page.
Model checks (Haiku on the worklist) run separately and are applied with the existing merge scripts.
"""
import json, re, subprocess, sys, time
from collections import Counter
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); T = R / "latex_tools"; A = R / "ocr_ab"
CP = Path(r"D:\MSA\msa_question_pipeline_v1.1\msa_pipeline\.claude\worktrees\distracted-sanderson-cae518\msa_question_pipeline_v1.1\msa_pipeline\claude_pipeline")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
sys.path.insert(0, str(CP))
P = int(sys.argv[1]); PP = f"{P:02d}"
QC = R / "qc" / f"paper_{PP}"; QC.mkdir(parents=True, exist_ok=True)
summary = []
say = lambda s: (summary.append(s), print(s, flush=True))


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=T, **kw,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode:
        raise SystemExit(f"FAILED {cmd}:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r.stdout


# ---- input / output versions
cands = sorted(R.glob(f"final/paper_{PP}_v*.json"), key=lambda f: int(re.search(r"_v(\d+)", f.name)[1]))
src = cands[-1] if cands else next((f for f in (R / "checked" / f"paper_{PP}_v5.json", R / "checked" / f"paper_{PP}.json") if f.exists()), None)
if not src:
    sys.exit(f"paper {P}: no stage file yet (run the DeepSeek stages first)")
ver = int(re.search(r"_v(\d+)", src.name)[1]) + 1 if "_v" in src.name else 5
if "--src" in sys.argv:  # a hand/fix-script edited copy of the newest stage: checks run on it, output is still the next version
    src_prev, src = src, Path(sys.argv[sys.argv.index("--src") + 1])
dst = R / "final" / f"paper_{PP}_v{ver}.json"
say(f"# Paper {P} QC  ({time.strftime('%Y-%m-%d %H:%M')})\ninput {src.name} -> output {dst.name}")
g = json.load(open(src, encoding="utf-8"))

# ---- 1 answer key: book answer pages (start = date heading, end = where numbering restarts) vs key vs solver
import cet_answers, cet_run
start = None
for pg in range(220, 428):
    f = R / "ocr" / f"page_{pg:03d}.txt"
    m = cet_run.DATE_HEAD.search(f.read_text(encoding="utf-8")) if f.exists() else None
    if m and (int(m[1]), m[2]) == (int(cet_run.paper_row(P)[1][-2:]), cet_run.paper_row(P)[2]):
        start = pg; break
key_issues = []
if start is None:
    say("1 answer key: answer pages not OCR'd yet (no date heading found) -> skipped")
else:
    end = start
    while end + 1 <= 427 and (R / "ocr" / f"page_{end + 1:03d}.txt").exists():
        _, anom = cet_answers.parse_pages(R / "ocr", range(start, end + 2))[:2]
        if any(a.startswith("dup") for a in anom):
            break
        end += 1
    book, _ = cet_answers.parse_pages(R / "ocr", range(start, end + 1))[:2]
    agree = 0
    for q in g["questions"]:
        k, b, s = q.get("correct_option"), book.get((q["subject"], int(q["number"]))), q.get("solved_option")
        if k == b and s in (None, k):
            agree += 1; continue
        human = any(p.get("op") in ("set_option_text", "set_stem") for p in q.get("patches", []))
        if b and b != k and b == s and not human:   # book page + solver agree against the key: fix the key
            q["edits"].append(dict(field="answer key", type="repair_key_qc", basis="answer_page+solver", before=k, after=b))
            q["correct_option"] = b; key_issues.append((q["qid"], f"KEY FIXED {k}->{b}"))
        else:
            key_issues.append((q["qid"], f"key {k} / answer page {b} / solver {s}"))
            if b and b != k: q.setdefault("flags", []).append("QC_KEY_VS_ANSWER_PAGE")
    say(f"1 answer key: pages {start}-{end}, {len(book)} answers read, {agree}/{len(g['questions'])} agree 3 ways, "
        f"{sum('FIXED' in x[1] for x in key_issues)} fixed, {sum('FIXED' not in x[1] for x in key_issues)} to review")
tmp0 = QC / "_stage0.json"; tmp0.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")

# ---- 2 LaTeX rules + canonical form
tmp1 = QC / "_stage1.json"
o1 = run([sys.executable, str(A / "latex_v9.py"), str(tmp0), str(tmp1)])
o2 = run([sys.executable, str(T / "canon.py"), str(tmp1), str(dst)])
say("2 LaTeX: " + o1.splitlines()[0] + " | canonical: " + o2.splitlines()[0])

# ---- 3 strict KaTeX gate
o3 = run(["node", str(T / "katex_check.js"), str(dst), str(QC / "katex.json")])
say("3 KaTeX strict: " + o3.splitlines()[0] + ("  GATE OK" if " 0 problems" in o3.splitlines()[0] else "  GATE FAILED: see katex.json"))

# ---- 4 units / duplicates / empty options
o4 = run([sys.executable, str(T / "units_check.py"), str(dst.relative_to(R))])
say("4 units/options: " + o4.splitlines()[0])

# ---- 5 text vs existing book text files
o5 = run([sys.executable, str(T / "text_compare.py"), str(dst.relative_to(R))])
(QC / "text_compare.txt").write_text(o5, encoding="utf-8")
say("5 text vs book text: " + o5.splitlines()[0])

# ---- 6 crops + model worklist (mismatches + key issues + flags)
run([sys.executable, str(A / "make_crops_all.py"), str(dst)])
rows = json.load(open(T / "text_compare_report.json", encoding="utf-8"))
need = {r["qid"] for r in rows if r["status"] == "MISMATCH" or r.get("blank_suspect")} | {q for q, _ in key_issues if "FIXED" not in _}
G = {q["qid"]: q for q in json.load(open(dst, encoding="utf-8"))["questions"]}
work = [dict(qid=k, crop=str(A / "crops_all" / f"{k}.png"), stem=G[k]["stem"], options=G[k]["options"], book_key=G[k].get("correct_option"),
             text_diff=next((r for r in rows if r["qid"] == k), {}), key_issue=dict(key_issues).get(k)) for k in sorted(need)]
(QC / "model_worklist.json").write_text(json.dumps(work, ensure_ascii=False, indent=1), encoding="utf-8")
say(f"6 model worklist: {len(work)} questions -> qc/paper_{PP}/model_worklist.json (Haiku checks only these)")

# ---- 7 review PDF + gold page
if "--no-pdf" not in sys.argv:
    prev = src_prev if "--src" in sys.argv else src
    html = R / "reconstructed" / f"paper_{PP}_v{ver}.html"; pdf = html.with_suffix(".pdf")
    run(["node", str(T / "build_pdf.js"), str(prev), str(dst), str(html)])
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", html.as_uri()], capture_output=True, timeout=600)
    run(["node", str(T / "gold_page.js"), str(dst), str(R / "reconstructed" / f"paper_{PP}_v{ver}_gold_set.html")])
    say(f"7 review PDF: {pdf.name}  + gold page")

for f in (tmp0, tmp1): f.unlink(missing_ok=True)
(QC / "report.md").write_text("\n".join(summary) + "\n\nkey issues:\n" + "\n".join(f"- {q}: {s}" for q, s in key_issues), encoding="utf-8")
print(f"\nreport: {QC / 'report.md'}")
