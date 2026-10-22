# Final architecture: scanned exam book -> verified question JSON (2026-10-22)

Principle: cheap local OCR does the reading, DeepSeek structures, repairs and flags, code does everything deterministic and is the gatekeeper, Claude looks only at what is flagged,
a human verifies at the end, and DeepSeek turns the human's rough notes into exact patches that code applies and logs. Every stage writes a versioned file and a backup; nothing is overwritten.

## 1. Flow

```
 PDF (scanned)
   |
 S0  render pages (code)                       -> pages/page_NNN.png            [v0]
   |
 S1  local OCR, DeepSeek-OCR on CPU (Ollama)   -> ocr/page_NNN.txt              [v1]  text + LaTeX + figure boxes
   |     loop detection + auto-retry, resumable queue, progress file
   |
 S2  parse (code): blocks, question numbers, subjects (numbering resets),
   |     answer pages -> "N. (X) Std.12 | Ch-N" lines, figure box list
   |     + confusion watchlist (code flags on raw OCR)
   |                                           -> parsed/paper_PP_blocks.json     [v2]
   |
 S3  DeepSeek repair-and-flag (API, one page per call, FINAL prompt)
   |     strip formatting, split options, repair symbols WITH evidence, solve check, flags
   |                                           -> deepseek/page_NNN.json           [v3]  PageResult schema
   |
 S4  code gates (the model is never the last word)
   |     word guard, repair gate, answer-key check (solved_option vs key),
   |     counts (50/50/100), option counts, figure assignment, watchlist hits not handled
   |                                           -> checked/paper_PP.json + queue.json [v4]  each question: ok | needs_claude
   |
 S5  figures (code): OCR boxes -> grow to ink edge -> split merged option boxes -> crop original + cleaned copy
   |                                           -> images_original/, images_regenerated/
   |
 S6  Claude, ONLY on needs_claude items (Sonnet, batched; Haiku for simple ones)
   |     input = question text + page crop of that question (+ answer-key line)
   |     output = fix or confirm; Opus audits one contact sheet per paper (figures + flag summary)
   |                                           -> checked/paper_PP_claude.json     [v5]
   |
 S7  optional figure regeneration (gpt-image-1-mini, medium) from the cleaned crop
   |     overlay gate vs the crop; fail -> keep the cleaned crop
   |
 S8  build final JSON (DocumentOut + CetQuestion fields), schema validate, zips, backup
   |                                           -> CET_PCB_2024_*.json, images zips   [v6 = FINAL CANDIDATE]
   |
 S9  HUMAN VERIFICATION against the PDF (review sheet: question | page crop | flags | repairs | key)
   |     output = rough error notes per file/paper (free text, one line per problem)
   |                                           -> human/errors_paperPP.txt
   |
 S10 DeepSeek patch step: rough notes -> exact structured patches (see section 4)
   |     code applies patches that match, logs every change, returns unclear notes to the human
   |                                           -> patches/paperPP_patches.json, final/ ... [v7 = FINAL]
   |
 S11 re-run watchlist + schema + counts on v7, export, backup, tag the release
```

## 2. Stage table

| Stage | Who | Cost | Time (14 papers) | Status |
|---|---|---|---|---|
| S0 render | code | free | minutes | DONE (all 428 pages, backed up) |
| S1 OCR | DeepSeek-OCR local | free | ~16 h more at 140-175 s/page (55/427 done) | RUNNING (laptop sleep stalls it; set never-sleep) |
| S2 parse + watchlist | code | free | minutes | built for paper 1 (`ocr_compare.py`, `confusion_watch.py`); answer-page parser TODO |
| S3 DeepSeek | DeepSeek API (`deepseek-reasoner` for maths/chem, `deepseek-chat` for prose) | cents per paper | ~1-2 min per page | prompt + schema DONE, API runner TODO, balance $0 |
| S4 gates | code | free | minutes | designed, TODO |
| S5 figures | code | free | minutes | partly built (`build_ocr_pdf.py` grow(), `cet_tools.py` crops); split-merged-boxes TODO |
| S6 Claude | Sonnet/Haiku, Opus audit | small (flagged only) | hours | agents + briefs exist for reading; checker brief TODO |
| S7 regeneration | gpt-image-1-mini | ~$65-175 per 10k figures | hours | optional; needs OpenAI credit; test of 5 figures pending |
| S8 build | code | free | minutes | built for the Sonnet-read path; rewire to v4/v5 |
| S9 human | people | 8-12 h estimated for 2,800 questions | days | review sheet TODO |
| S10 patch | DeepSeek + code | cents | minutes | designed here, TODO |

## 3. Data layout and versioning
```
D:\MSA\outputs\cet_pcb_2024\
  pages\            v0 page PNGs                 ocr\          v1 raw OCR text      ocr_progress.json, flagged.txt, ocr_errors.txt
  parsed\           v2 blocks + answers          deepseek\     v3 PageResult JSON (+ raw replies)
  checked\          v4 gate results, v5 Claude   images_original\ images_regenerated\
  papers\           v6 per-paper DocumentOut     human\        errors_paperPP.txt (rough notes), review_log.json
  patches\          S10 patch files              final\        v7 release files + zips
  backup\           timestamped copy of every stage output before it is replaced
```
Rule: a stage writes `vN` next to the previous one and a timestamped copy to `backup\`; a rerun of any stage never touches earlier versions. Every question carries `qid`, its source page and block indexes, so any value can be traced back to the OCR block and the page.

## 4. Human verification and the DeepSeek patch loop (S9, S10)

**Human output (rough, per file):** one line per problem, free text. Examples:
```
paper 3 Physics 12 stem: n should be pi
paper 3 Physics 40 option C: should be 2pi/mu0 sqrt(A^3)
paper 5 Chem 18: option pictures are merged, B and C swapped
paper 7 Bio 33 answer is C not B
paper 2 Physics 5 figure crop cuts the label O
```
**DeepSeek input:** the note, plus the current JSON of just that question (stem, options, answer, flags). It returns one patch per note.

**Patch schema** (validated by code):
```
{"patches":[{
  "note": "<the human line, verbatim>",
  "qid": "CET_PCB_2024_P03_Phy_q012",
  "op": "replace_text | set_option_text | set_correct_option | set_stem | delete_question | figure_fix | needs_clarification",
  "field": "stem | options.A..D | correct_option | figure",
  "before": "<exact current text the human is talking about>",
  "after": "<new text, LaTeX in $...$, units with \\mathrm>",
  "confidence": 0-1,
  "ambiguity": "" }]}
```
**Rules and guard (code):**
1. DeepSeek only translates the note; it adds nothing the note does not say. A note it cannot turn into an exact change becomes `needs_clarification` and goes back to the human.
2. A patch is applied only if `before` matches the current text exactly (or the note names the whole field); otherwise it is rejected and returned.
3. `figure_fix` and `set_correct_option` are applied by code directly from the note (box edits re-crop; answer letters validated as A-D) and never rewritten by the model.
4. Every applied patch is logged (`review_log.json`: who/when/note/before/after), the previous version is backed up, and the question gets `human_verified: true`.
5. After patching: schema validation, counts per subject, watchlist, and the answer-key check run again; any new problem is reported back to the human.
6. Conflicting patches for the same field: the later note wins and the earlier one is listed for confirmation.

## 5. Per-question record (what each stage adds)
`qid, paper, exam_date, shift, subject, number, page, column, stem, options{A-D}, correct_option, chapter_tag, question_images[], option images, difficulty/topic/subtopic (blank), flags[{code,field,severity,detail}], edits[], status (clean|fixed|repaired|flag), solvable, solved_option, confidence, human_verified, source_blocks`.
Solutions are not kept; only the correct option.

## 6. Quality gates
- **Gate A (after S4, per paper):** counts 50/50/100, four options each, answer letters A-D, no unresolved fatal flag outside the Claude queue.
- **Gate B (pilot, before scaling):** paper 1 fully through S1-S8, 60-question human sample: fatal <= 0.5%, all errors <= 1% after Claude, 0 wrong answer letters after the key cross-check, option pictures split correctly; then paper 2 as a generalisation check.
- **Gate C (after S9/S10):** zero open `needs_clarification`, zero schema errors, zero watchlist or key mismatches left unreviewed.
- Reviewer effort is tracked (minutes per flagged question) as the cost metric.

## 7. Severity scale used everywhere
`high` = fatal (the student cannot reach the intended answer: wrong/undefined given, wrong digit/exponent/sign/operator, missing or merged option, missing figure, wrong correct-option letter, lost question), `medium` = harmful but solvable (lost subscript, Greek letter misread, lost hats), `low` = cosmetic. Review order: high, then medium, then a sample of the rest.

## 8. Failure handling
- OCR loop or crash: auto-retry with a stronger repeat penalty, then flag the page; queue waits and retries the same page if Ollama is down; progress file; resumable.
- DeepSeek reply invalid or breaks the word guard: retry once, then the page goes to Claude.
- Every API call is idempotent per page; raw replies are saved for replay.
- Keys only from environment variables; never written to files; keys pasted in chat are rotated.

## 9. Risk register (top)
1. Wrong correct-option letters (answer extraction): OCR + code parse of the answer pages, DeepSeek solved_option cross-check, human sees the key line. Found one error already (Physics 1).
2. OCR inventions and lost symbols that text cannot reveal (Q40 option C): second reader/Sonnet on flagged and math-heavy options, human sample.
3. Figures (merged option pictures, wrong association): code splitting and counts, Opus contact-sheet audit, human review.
4. Throughput on a CPU laptop: ~25-28k questions per week at best; fine for 2,800 questions, not for 2 lakh without a GPU or other hardware.
5. Costs and balances: DeepSeek $0, OpenAI $0, Kling $0 at last check.

## 10. Build order from here
1. Keep the OCR queue running (never-sleep power setting). 2. Answer-page OCR + parser, cross-check against the current key. 3. DeepSeek API runner (validate, retry, save raw) once the balance exists.
4. Gates (S4). 5. Figure association and splitting (S5). 6. Claude checker brief + batching (S6). 7. Builder rewired to v4/v5 (S8). 8. Review sheet (S9). 9. Patch step (S10).
10. Pilot on paper 1, Gate B, then paper 2, then the remaining papers. 11. Optional regeneration (S7) after its 5-figure test.

## Update 2026-10-22: page-truth read added (after the residual-error sample)
Finding: about 3% of UNFLAGGED questions carry a fatal-type OCR error (PCl3 for PCl5, "lg" for "1 g", rho read as p, lost subscripts); flags cannot catch them. New stage S2b: PAGE-TRUTH READ of every question (Sonnet, question crop + OCR draft, ~1,750 tokens per question) producing page-faithful text. Order now: S1 OCR -> S2 parse/watchlist -> S2b page-truth read (Sonnet) -> S3 DeepSeek -> S4 gates -> S5 figures -> S6 Claude on conflicts -> S7 regen -> S8 build -> S9 human -> S10 patch.
DeepSeek's role after S2b: formatting and LaTeX normalisation, option splitting, consistency checks, the solve check (solved_option vs answer key), flags. Its repair mode becomes a suggestion only: the page-truth text wins; any DeepSeek change to page-truth text is not applied automatically but routed to Sonnet/human. Cheaper alternative to a full Sonnet read: a second independent OCR pass and a page-truth read only where the two disagree.

## Decision 2026-10-22 (user): low-Sonnet pipeline
S1 OCR -> S3a DeepSeek GAP CHECK (finds what the OCR lost: missing options, cut stems, undefined symbols; returns a gap list, no edits) -> S3b DeepSeek COMPLETION PASS (told "this is the complete question": find the mistakes and complete or slightly change them; repair-mode prompt) -> S4 code gates -> S6 Haiku OVERVIEW (question crop + text, confirm or correct; Sonnet only on Haiku disagreements) -> figures: Sonnet only for association/crop checks and the contact-sheet audit -> S7 regeneration by gpt-image-1-mini behind the overlay gate -> S8 build -> S9 human -> S10 patch.
Known limit: DeepSeek cannot see the page, so digit/letter-level OCR errors (PCl3 for PCl5, lg for 1 g, rho for p) are only caught by the Haiku/Sonnet overview. Open test: how many of the 189 page-truth-labelled questions (corrections_pilot.json 109 + corrections_sample.json 80) does a Haiku overview catch, and how many false changes does it make.
