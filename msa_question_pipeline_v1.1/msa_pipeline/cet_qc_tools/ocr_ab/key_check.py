"""Answer-key check for paper 1: v7 key vs OCR'd answer pages (220-237) vs DeepSeek solved_option. Code only."""
import json, sys
from pathlib import Path
R = Path(r"D:\MSA\outputs\cet_pcb_2024")
sys.path.insert(0, r"D:\MSA\msa_question_pipeline_v1.1\msa_pipeline\.claude\worktrees\distracted-sanderson-cae518\msa_question_pipeline_v1.1\msa_pipeline\claude_pipeline")
from cet_answers import parse_pages

ocr_key, anomalies = parse_pages(R / "ocr", range(220, 234))[:2]
v7 = json.load(open(R / "final" / "paper_01_v7.json", encoding="utf-8"))
qs = v7["questions"] if isinstance(v7, dict) else v7
# solved_option lives in the DeepSeek stage file, not in the final JSON
solved = {}
for f in [R / "checked" / "paper_01.json", R / "checked" / "paper_01_v5.json"] + [R / "final" / "paper_01_v7.json"]:
    d = json.load(open(f, encoding="utf-8")); d = d["questions"] if isinstance(d, dict) else d
    for x in d:
        if isinstance(x, dict) and x.get("solved_option"):
            solved[x["qid"]] = x["solved_option"]
rows, agree3 = [], 0
for x in qs:
    s, n = x["subject"], x["number"]
    k, o, ds = x.get("correct_option"), ocr_key.get((s, n)), solved.get(x["qid"])
    if k == o and (ds in (None, k)):
        agree3 += 1; continue
    rows.append(dict(qid=x["qid"], key=k, ocr_answer_page=o, deepseek_solved=ds,
                     kind="KEY_vs_ANSWER_PAGE" if o and o != k else ("NO_ANSWER_PAGE_READ" if not o else "DEEPSEEK_DISAGREES")))
out = dict(questions=len(qs), answer_page_entries=len(ocr_key), deepseek_solved=len(solved), all_agree=agree3,
           anomalies=anomalies, issues=rows)
(R / "ocr_ab" / "key_check_paper01.json").write_text(json.dumps(out, indent=1))
from collections import Counter
print({k: v for k, v in out.items() if k not in ("issues", "anomalies")}, Counter(r["kind"] for r in rows), "anomalies:", len(anomalies))
for r in rows:
    if r["kind"] == "KEY_vs_ANSWER_PAGE": print(r)
