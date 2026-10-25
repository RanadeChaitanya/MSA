# CET PCB 2024 — Paper 1 (22 April, Shift I) — final version v12

200 questions (Physics 50, Chemistry 50, Biology 100). Every question was checked against the book scan (rendered-vs-scan image check, all 200).

| File | What it is |
|---|---|
| `paper_01_original.json` | **Deliverable** (CetDocument): text, options A-D, correct option, question/option images (cropped from the scan), flags, `release`, `book_key`, `review_note` |
| `paper_01_regenerated.json` | Same, pointing at the cleaned/regenerated images |
| `paper_01_images_original.zip` / `_regenerated.zip` | The 18 images the JSONs reference (10 question figures + 8 option pictures) |
| `paper_01_v12.json` | Working stage file with full edit history (`edits`, `patches`, `latex_fixes`) |
| `paper_01_v12.pdf` | Review PDF: change log, then every question with the scan (left) and our KaTeX rendering (right) |
| `paper_01_v12_gold_set.html` | 50 most formula-heavy questions for a human check (open locally, tick differences, "Copy my list") |
| `option_images/` | Option pictures for Chem 18 and Chem 27 |
| `qc_report.md` | Output of `cet_qc_tools/latex_tools/cet_qc.py 1` |

## Status
- **Held back (`release: false`)**: Phy 3, Phy 4, Bio 89 — defective in the printed book.
- **Book misprint corrected**: Chem 40 key D -> C (`book_key` keeps D).
- **Judgement calls kept with notes (`review_note`)**: Phy 32, Che 21, Bio 19, 52, 53, 55, 66, 92.
- Kept chemically/physically correct where the book misprints: Chem 24 (NO3^-), Phy 37 (degC^-1).

## Checks (all pass)
Strict KaTeX 478/478 · units/options 0 issues · answer key 186/200 three-way agreement, rest decided · data audit (`audit_paper.py 1`) 0 issues · QC rerun changes nothing.

Estimated accuracy ≈ 99%+ (all errors found by the full image check are fixed). Not yet human-verified end to end.

Rebuild after any pipeline build: `python cet_qc_tools/latex_tools/enrich_deliverable.py 1` (until the pipeline build carries release/book_key/review_note itself).
