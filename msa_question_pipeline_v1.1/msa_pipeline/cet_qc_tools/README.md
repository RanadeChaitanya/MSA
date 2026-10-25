# CET QC tools (Paper 1: v7 -> v12, 2026-10-24/25)

Code-only quality layers for the CET PCB 2024 question JSONs. Paths inside the scripts point at `D:\MSA\outputs\cet_pcb_2024\`.

One command per paper (after the DeepSeek stages produced `checked/paper_PP*.json`):

    python latex_tools/cet_qc.py P [--no-pdf] [--src fixed.json]

Layers: answer key 3-way check (answer pages from the date heading to where numbering restarts) -> LaTeX rules (`ocr_ab/latex_v9.py`) + canonical form (`canon.py`: mhchem chemistry, `\mathrm` units, `\text` words, statements one per line) -> strict KaTeX gate (`katex_check.js`, needs `npm install` here) -> units/empty/duplicate options (`units_check.py`) -> text vs book OCR txt: numbers, symbols, words, lost blanks (`text_compare.py`) -> crops + `qc/paper_PP/model_worklist.json` for Haiku -> KaTeX review PDF (`build_pdf.js` + Edge) + gold page (`gold_page.js`).

Image check (rendered vs scan): `render_q.js` + Haiku on `visual_batch_*.json`; per-version fixes in `fix_v11.py`, `fix_v12.py`.
