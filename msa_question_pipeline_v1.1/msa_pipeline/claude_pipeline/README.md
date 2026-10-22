# CET PCB 2024 question pipeline (scanned book -> verified question JSON)

Scanned book `CET PCB 2024 (2).pdf` (428 pages, no text layer) -> `DocumentOut` JSON + cropped figure zips, target >= 99% after a human review pass. Design: `ARCHITECTURE.md`. Current state and open items: `HANDOVER.md`. Trial work: `ARCHIVE_INDEX.md`.
Data lives outside the repo in `D:\MSA\outputs\cet_pcb_2024\`; keys come only from environment variables (`DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`), never from files.

## Entry points
| Command | Stage | Needs |
|---|---|---|
| `python claude_pipeline/render_pages.py` | S0 render PDF pages to PNG | PyMuPDF |
| `python claude_pipeline/ocr_queue.py` | S1 local DeepSeek-OCR via Ollama (resumable, loop detection) | Ollama + model, CPU |
| `python claude_pipeline/cet_run.py preflight P` | checklist: OCR pages, answer key, keys, DeepSeek balance | |
| `python claude_pipeline/cet_run.py all P [--no-claude] [--force]` | S3-S8 for paper P, stops before the human step | both keys |
| `... cet_run.py deepseek P` | S3 DeepSeek repair-and-flag, schema-validated (`deepseek_prompt_FINAL.md`, `deepseek_schema.py`) | `DEEPSEEK_API_KEY` |
| `... cet_run.py gate P` | S4 code gates: word guard, repair gate, answer-key check, counts, watchlist, figures | |
| `... cet_run.py claude P [--all]` | S6 Haiku detector + code applier on flagged questions | `ANTHROPIC_API_KEY` |
| `... cet_run.py review P` | S9 HTML review sheet (high flags first) | |
| `... cet_run.py reconstruct P [--flagged]` | strict rebuild of each question from data into a PDF (`cet_render.py`) | |
| `... cet_run.py feedback P` | S10 human notes (`human/errors_paperPP.txt`) -> DeepSeek patches -> code applies exact matches only, logs, verifies | `DEEPSEEK_API_KEY` |
| `... cet_run.py cost P` | tokens and dollars for paper P: per step, per API/model, per question, per step per question, real DeepSeek spend from balance snapshots, 14-paper projection. Every API call is logged to `usage/usage.jsonl`; prices in `prices.json` (verify them) | |
| `... cet_run.py build P` | S8 `DocumentOut` JSON + original/regenerated image zips from the newest stage file | |
| `python claude_pipeline/overlay_gate.py` | S7 gate for an optional regenerated figure (regeneration itself is not wired in) | |

## Checks
- `python claude_pipeline/test_cet_run.py` offline end-to-end test (stub DeepSeek, temp output dir; no keys, no network).
- `python claude_pipeline/parser_regression.py` segmentation, option and watchlist regression on the OCR'd papers.

## Modules
`cet_tools.py` paper table, models, crops, legacy build; `cet_answers.py` answer-page parser (+ chapter tags); `cet_options.py` option splitter and integrity flags; `cet_figures.py` merged-picture splitter and `grow()`; `confusion_watch.py` OCR-confusion watchlist and question segmenter; `cet_render.py` strict reconstruction; `prices.json` API price table; `run_pilot.py` earlier Sonnet/Opus API pilot (kept as committed).
