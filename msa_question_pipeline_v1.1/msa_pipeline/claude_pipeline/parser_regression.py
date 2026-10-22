"""Regression test for the question segmenter on real OCR pages (no model, no network).
  python claude_pipeline/parser_regression.py
Each paper: (ocr dir, first page, last page, expected {subject: count}, allowed missing {subject: [numbers]}, allowed inferred [(subject, number)]).
A change to confusion_watch.questions() must keep every line PASS. Known gaps are listed so they cannot grow silently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import confusion_watch as cw

BASE = r"D:/MSA/outputs/cet_pcb_2024/"
FULL = {"Physics": 50, "Chemistry": 50, "Biology": 100}
CASES = {
    "paper 1": (BASE + "ocr_p1", 1, 16, {}, [], []),
    "paper 2": (BASE + "ocr", 17, 32, {"Chemistry": [46]}, [], [("Physics", 30), ("Chemistry", 43)]),  # OCR dropped numbers 30, 43; 46 not recoverable by structure
    "paper 3": (BASE + "ocr", 33, 49, {}, [], []),
    "paper 4": (BASE + "ocr", 50, 64, {}, [], []),
}
bad = 0
for name, (d, a, b, missing_ok, _, inferred_ok) in CASES.items():
    qs = cw.questions(d, a, b)
    problems = []
    for s, n in FULL.items():
        got = {q["number"] for q in qs if q["subject"] == s}
        miss = sorted(set(range(1, n + 1)) - got)
        if miss != sorted(missing_ok.get(s, [])):
            problems.append(f"{s} missing {miss} (allowed {missing_ok.get(s, [])})")
        dup = len([q for q in qs if q["subject"] == s]) - len(got)
        if dup:
            problems.append(f"{s} has {dup} duplicate numbers")
    inf = sorted((q["subject"], q["number"]) for q in qs if q["inferred"])
    if inf != sorted(inferred_ok):
        problems.append(f"inferred {inf} (allowed {sorted(inferred_ok)})")
    print(f"{name}: {'PASS' if not problems else 'FAIL'}  questions {len(qs)}  {'; '.join(problems)}")
    bad += bool(problems)
# option splitter: papers 1-4 (800 questions). Known gaps = options the OCR itself lost (incomplete); must not grow. status counts measured 2026-10-22.
import cet_options as co
inc = sum(1 for a, b in ((1, 16), (17, 32), (33, 49), (50, 64)) for q in cw.questions(BASE + "ocr", a, b) if co.split_options(q["text"])["status"] == "incomplete")
print(f"options: incomplete (OCR lost content, flagged) {inc} (allowed <= 18): {'PASS' if inc <= 18 else 'FAIL'}")
bad += inc > 18
sys.exit(1 if bad else 0)
