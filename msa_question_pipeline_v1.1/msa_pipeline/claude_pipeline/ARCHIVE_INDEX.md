# Archive index: trial and test work moved out of the repo (2026-10-22)

Everything below was moved, unchanged, to `D:\MSA\archive\cet_trials_2026-10-22\` (outside git; nothing deleted). The repo keeps only the pipeline, its checks and its docs (see `README.md`).
`TESTING.md` there (46 KB) is the full log of 27 test sections; the findings that still matter are summarised at the end of this file.

## Moved files
| Archived item | What it was | Outcome |
|---|---|---|
| `agents/` (answer_reader, question_reader, detector, applier, page_truth_corrector, haiku_sense_repair, test_and_improve) | Chat-agent briefs used to read pages with Claude and to test a detector/applier design | Superseded by the API stages in `cet_run.py`; the detector prompt lives in `cet_run.DETECT_RULES` |
| `run_pilot_chat_mode_edits.py` | Uncommitted `--drafts-only/--assemble` edits to the older `run_pilot.py` | Older Sonnet/Opus pilot; repo copy of `run_pilot.py` restored to the committed version |
| `heavy_test_cet.py`, `run_on_available.py`, `deep_audit.py`, `audit_detail.py`, `risk_set.py` | Code-only audits of OCR text, risk-set sizing | Findings folded into `confusion_watch.py` and `cet_options.py` |
| `compare_haiku.py`, `compare_runs.py`, `false_changes.py`, `check_known_errors.py`, `score_corrections.py`, `strict_compare.py`, `make_inputs_189.py` | Scoring of Haiku/Sonnet readers against the 189 page-verified questions | Haiku two-step: 55% catch, 18% false changes; one-step 42% / 12% |
| `ocr_compare.py`, `build_ocr_pdf.py`, `ocr_rebuild_5q.pdf`, `ocr_rebuild_preview.png`, `none.json`, `ocr_pages_01-20.md` | OCR-vs-Sonnet comparison and the first OCR-only PDF rebuild | Replaced by `cet_render.py` (strict reconstruction from pipeline data); `grow()` moved to `cet_figures.py` |
| `show_q.py`, `flag_montage.py`, `crop_ocr.py`, `ocr_page.py` | Inspection helpers, single-page OCR | `ocr_queue.py` is the OCR runner |
| `make_deepseek_input.py`, `deepseek_inputs/`, `deepseek_prompt.md`, `deepseek_prompt_repair.md` | Earlier DeepSeek chat-UI inputs and prompt versions v1-v4 | Merged into `deepseek_prompt_FINAL.md`; inputs now built in `cet_run.page_blocks` |
| `deepseek_check.py` | Key/balance probe | Balance check is in `cet_run.py preflight` |
| `vtracer_test.py`, `omnisvg_space.py`, `omnisvg_che27.png`, `decimer_test.py`, `detikzify_space.py`, `hf_test.py`, `probe_space.py`, `kling_test.py`, `kling_app_test/`, `openai_test.py` | Figure-redraw and image-model experiments | VTracer, Haiku/Sonnet SVG and OmniSVG rejected; cleaned crop is the default; gpt-image-1-mini stays an optional later step; Kling/DeTikZify/StarVector untested or blocked by balances |
| `backup_scripts/` | Timestamped copies of earlier `cet_options`, `confusion_watch`, `parser_regression` | Superseded by the current files |
| `TESTING.md` | Log of test sections 1-27 | See summary below |

## Findings worth keeping (details in the archived TESTING.md)
- Parser segmentation 799/800 questions on papers 1-4; options 98% vs the Sonnet read; 34/799 questions have options the OCR lost (flagged by `cet_options.py`).
- About 3% of unflagged questions still carry a fatal-type OCR error (PCl3 for PCl5, "lg" for "1 g", rho read as p, lost subscripts and signs): only reading the page catches them, hence the Claude detector stage.
- Readers vs page-truth on 189 questions: Haiku one-step 42% catch / 12% false changes; "free to change" prompt 49% / 46%; Haiku two-step (detector + applier) 55% / 18%.
- Answer key: Sonnet read had Physics 1 of paper 1 wrong (B, book prints A), found by the DeepSeek `solved_option` cross-check.
- PDF-Extract-Kit scored 20% image recall and was dropped; Landing AI/Docling pipelines live in other repos.
- Redraw: cleaned crop is the safe default for figures; regeneration must pass `overlay_gate.py`.
