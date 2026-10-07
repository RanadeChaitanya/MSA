# MSA Question Pipeline — PDF (clean *or* hand-clicked photos) → structured question JSON

Turns question-paper PDFs into JSON where every question, every option, and every diagram/option-image is
linked and traceable to exact pixels. Landing AI (ADE Gen2 / DPT-3) makes the layout and text decisions;
a few deterministic local steps (page repair, cropping, validation) surround it.

> **Status — read this first.**
> * Built and tested **offline** against a fake Landing AI client that reproduces the *documented* Gen2
>   response shapes (`structure` tree, markdown ranges, Extract `spans`). 7/7 tests pass.
> * **Not yet run against the real API** (the build sandbox cannot reach it and holds no key). First real run =
>   section 15 "First real run checklist". Expect small tuning, not redesign.
> * Your API key goes in `.env` (`LANDING_API_KEY=APIKEYHERE` is a placeholder; the client refuses to start with it).

---------------------------------------------------------------------------------------------------

## 1. Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# put your key in .env  ->  LANDING_API_KEY=...
python scripts/demo_offline.py                      # sanity check, no API needed
python -m pipeline.cli run my.pdf --stop-after canonicalize   # look at data/<doc>/canonical/*.png first
python -m pipeline.cli run my.pdf --pages 1-2       # real run on 2 pages (spike)
python -m pipeline.cli run my.pdf                   # full run
pytest -q                                           # tests
```
Outputs land in `data/<doc_id>/`:
`canonical/` (page images) · `lat_raw/` (raw API responses) · `assets/q_0001/question_1.png, option_B.png …` ·
`output/questions.json` · `output/review_queue.json` · `output/pages.json` · `debug/overlays/overlay_page_0001.png`.

**Always open the overlays after a run.** Blue = question region, green = option cells, red = question image,
orange = option image, red text = flags. They show every decision the pipeline made.

---------------------------------------------------------------------------------------------------

## 2. Final architecture (your diagram → implementation)

```
PDF
 └─[0 CANONICALIZE]  embedded photo at native res, else render 300 DPI; bake EXIF; rectify perspective (photos);
                     de-shadow; deskew; blur/resolution gate.  ==> ONE canonical PNG per page (THE coordinate space)
 └─[1 PARSE]         canonical PNGs -> batch PDF (<=BATCH_PAGES) -> POST /v2/parse (DPT-3 Pro)
                     => markdown + structure tree (blocks with page, range, box [+ atomic grounding])
 └─[2 SEMANTICS]     POST /v2/extract(markdown, schema) => question stem / options A-D / correct option + spans
                     spans overlapped with block ranges (client-side grounding) => which blocks belong to which question
 └─[3 BOXES]         QUESTION BOX RETRIEVAL : gutter numbers -> column regions            (stages/segment.py)
                     OPTION BOX RETRIEVAL   : A/B/C/D label glyphs -> option cells         (stages/options.py)
 └─[4 ROUTER]        per question: text-only | question-image | option-image | both
                     figure blocks + residual-ink check (+ math policy for options)        (stages/visuals.py)
 └─[5 REPASS]       (gated) tight upscaled crop of region -> POST /v2/parse as image -> figure boxes
                     mapped back: page_px = crop_origin + norm_box * crop_size
 └─[6 CROP]         crop from the SAME canonical image -> assets/…png, sha256, bbox, method   (stages/crop.py)
 └─[7 LINK/JSON]    qid / asset ids / options keyed by LABEL                                  (stages/assemble.py)
 └─[8 VALIDATE]     flags -> retry ladder (alt canonicalization profiles) -> review queue
```
Your diagram's four branches map to stage 4/5: **text-only** (no figure, no ink → JSON directly), **question image**
(figure/ink in the stem area → question box → repass → extract image → question list), **option image** (figure/ink
inside an option cell → option box → repass → option image), **both** (the two run independently on the same
question; an image can never hold two roles — validated).

### Key design invariants
1. **One canonical image per page.** The same PNG is uploaded to Landing AI and cropped for assets. Never mix
   spaces (never send the original PDF while cropping a preprocessed render).
2. **Landing AI decides semantics; geometry is deterministic.** Extract returns text; boxes come from Parse blocks.
3. **Images are always cropped locally.** Parse returns descriptions/boxes of figures, not pixels.
4. **Options are keyed by letter**, never by list position.
5. **Every asset is traceable:** `page`, `bbox_px`, `method`, `source_block_ids`, `sha256`.

---------------------------------------------------------------------------------------------------

## 3. Verified Landing AI contract (Gen2 / DPT-3) used here

| Call | Request | Response used |
|---|---|---|
| `POST {BASE}/v2/parse` | multipart `document` (PDF/PNG), `model`, `options` (JSON string) | `markdown`, `structure` (pages → blocks), `metadata.failed_pages`, billing |
| `POST {BASE}/v2/extract` | multipart `markdown` (file part), `schema` (JSON string), `model`, `options={"strict":false}` | `extraction`, `extraction_metadata` (per leaf `{value, spans}`), warnings, `schema_violation_error` |
| `POST {BASE}/v2/ground` | JSON `extraction_metadata` + `structure` | **not used by default** (client-side overlap is free, one fewer call, works for ZDR orgs; method kept in client) |

* Auth: `Authorization: Bearer <key>`. Status: 200 ok · **206 partial (`failed_pages`, 1-indexed)** · 422 validation · 429 rate limit.
* Structure: pages are 1-indexed; boxes are fractions `{xmin,ymin,xmax,ymax}`; block ids like `text-3`, `figure-0`
  (**not stable across re-parses** — never persist them as keys; assets store `source_block_ids` for traceability only).
* PDFs are parsed one page at a time independently; images are single-page. EXIF orientation is applied by the API,
  therefore canonicalization **bakes orientation into pixels and strips EXIF**.
* Extract `spans` are `[start,end)` code-point offsets into the exact markdown you send. Therefore all pages of a
  batch share one markdown, and Extract always receives the Parse markdown unmodified.
* Sync upload cap 50 MiB (we split batches above `MAX_UPLOAD_MB`), ~179 MP per image.

**Unverified until your first run** (adjust in `pipeline/lat/client.py` / `.env` if the API disagrees):
parse option names (`inline_markdown`, `atomic_grounding`), model aliases (`dpt-3-pro-latest`, `dpt-3-verity-latest`),
async Parse Jobs (not implemented; sync batches are used), Extract support for array-of-object schemas.

---------------------------------------------------------------------------------------------------

## 4. Folder structure and the role of every file

```
msa_pipeline/
├── .env                      your secrets/config. LANDING_API_KEY=APIKEYHERE (placeholder) + all tunables
├── .env.example              same, safe to commit
├── .gitignore                ignores .env, data/, caches
├── requirements.txt          pinned-minimum deps (requests, pydantic, pymupdf, opencv-headless, numpy, pillow, pytest)
├── README.md                 this file
├── pipeline/
│   ├── __init__.py           version
│   ├── config.py             Settings dataclass; loads .env; every threshold/policy lives here
│   ├── models.py             Pydantic contracts: PageRecord, AssetRef, OptionOut, QuestionOut, DocumentOut
│   ├── cli.py                `python -m pipeline.cli run ...` entrypoint
│   ├── runner.py             orchestrator: stages 0→8, retry ladder, outputs, run report, review queue
│   ├── lat/
│   │   ├── client.py         Landing AI REST client: multipart/JSON, retries+backoff, Retry-After, disk cache,
│   │   │                     credit counter, refuses placeholder key, never caches 206 partials
│   │   └── schemas.py        Extract JSON schema (number, stem, option_a..d, correct_option)
│   ├── stages/
│   │   ├── canonicalize.py   stage 0: embedded-photo vs render, rectify, de-shadow, deskew, blur gate, profiles
│   │   ├── parse_stage.py    stage 1: batch PDFs, size splitting, partial-failure page retry, BatchResult
│   │   ├── structure.py      flatten structure -> pixel-space Block objects; watermark marking; text cleaning
│   │   ├── segment.py        stage 3a: question regions from gutter numbers (cluster, consecutive, gap guards)
│   │   ├── options.py        stage 3b: option anchors -> option cells; stem/option/answer bucketing; table fallback
│   │   ├── semantics.py      stage 2: Extract + client-side span grounding -> binds extracted questions to regions
│   │   ├── visuals.py        stage 4/5: routing, residual-ink check, region repass, math-option policy, ImageCache
│   │   ├── crop.py           stage 6: padded crop from canonical image, sha256, AssetRef
│   │   ├── assemble.py       stage 7: QuestionDraft building + materialize (qids, crops, JSON models)
│   │   ├── validate.py       stage 8: flags (error vs warning), numbering, assets, confidence score
│   │   └── qa_confidence.py  optional Verity word-confidence pass (low_sharpness pages or QA_VERITY=true)
│   ├── retry/ladder.py       page-level retry ladder: alt canonicalization profiles, keep best attempt
│   ├── debug/overlay.py      draws every decision on the pages (the main debugging tool)
│   └── utils/                geometry.py (Box math) · io.py (hash/json/slug) · log.py
├── tests/                    fake_lat.py (offline Landing AI), make_pdf.py (synthetic photo-PDF),
│                             test_end_to_end.py, test_units.py
├── scripts/demo_offline.py   offline demo producing assets + overlays
└── data/                     runtime output (git-ignored)
```

---------------------------------------------------------------------------------------------------

## 5. Stage details

### 5.0 Canonicalize (`stages/canonicalize.py`)
* If a page is one image covering ≥85% of the page and ≥`EMBEDDED_MIN_LONG_SIDE` px → use the **embedded pixels
  untouched** (no re-render blur). Else render at `RENDER_DPI` (cap `MAX_LONG_SIDE`).
* Only for embedded rasters (photos/scans): perspective rectification (`PHOTO_RECTIFY=auto|off|force`; `auto` skips
  flat axis-aligned pages so it can't crop real content), illumination normalisation (never binarises),
  small-angle deskew (0.3°–8°). Homography stored in `PageRecord`.
* Quality gate flags `low_sharpness`, `low_resolution`.
* **Profiles** (`no_rectify`, `force_rectify`, `hires`) are what the retry ladder tries; each writes
  `page_0003__hires.png` so a rejected attempt never overwrites an accepted page image.
* Not handled (needs a dewarping model): strongly curved book pages. They parse if text lines stay readable,
  otherwise they land in the review queue.

### 5.1 Parse (`stages/parse_stage.py`)
Canonical PNGs → one lossless image-only PDF per batch (page size ∝ pixel size). 206 partial → failed pages are
re-parsed alone (up to 2 times) as their own batches.

### 5.3a Question boxes (`stages/segment.py`)
Candidates = small, pure-digit text blocks (`10`, `10.`, `10)`; also `N. text` starts). Clustered by left edge →
columns. Within a column the **longest chain of consecutive numbers ≥ `MIN_ANCHOR_GAP_FRAC`·H apart** wins; this is
what rejects digits of stacked fractions (`7/2`, `2:3`). A lone number in a column is accepted only if it continues
numbering from another column / the previous page. Region = number → next number in the same column; the last
region gets `LAST_REGION_PAD_FRAC` extra height so unparsed images hanging below the last block are still inside.
Watermark blocks (pattern or >25% page area) and the title banner are ignored.

### 5.3b Option boxes (`stages/options.py`)
Anchors = blocks that are exactly `A`/`(A)`/`A.` etc., outside any figure, one per letter (top-most). Rows clustered
by y, cells extend to the next anchor right/below → handles 2×2, 1×4, 4×1. Blocks are bucketed by centre. The
"Correct Option" marker and everything below it become `answer_text`/`correct_option`, never an option.
Fallback: the 4 cells of a table, row-major (`options_from_table_cells`). Else flag `no_option_anchors`.

### 5.2 Semantics (`stages/semantics.py`)
Extract on the Parse markdown. Each field's spans are overlapped with leaf block ranges; the region holding most of
those block centres claims the question (one-to-one). Fallback: printed number. Missing → text falls back to
block markdown and flag `extract_missing`.

### 5.4/5.5 Visual routing + repass (`stages/visuals.py`)
* Question image: figure blocks in the stem area; else **residual ink** (dark components not explained by text/
  figure boxes; ruling lines, table frames and tiny specks excluded) → **repass** the padded crop (upscaled to
  `REPASS_MIN_SIDE`) → figure box(es) → mapped to page px; if repass finds nothing → `ink_fallback` crop (flagged).
* Option image: figure blocks in the option cell; else residual ink → repass; else policy `OPTION_IMAGE_MODE`:
  `visual_only` · `visual_or_math` (default: also crop options whose text contains LaTeX/sup/sub or is empty) · `always`.
  `image_only=true` when an option has an image and no text.
* Repass is the only extra paid call and fires only when parse missed something visible.

### 5.6/5.7 Crop + link (`stages/crop.py`, `assemble.py`)
Padded crop of the canonical image → `assets/q_0003/option_C.png`. IDs: `q_0003`, `q_0003_img_1`, `q_0003_opt_C`.

---------------------------------------------------------------------------------------------------

## 6. Output JSON (`output/questions.json`, abridged)

```json
{
  "doc_id": "paper_1a2b3c4d",
  "page_count": 4,
  "questions": [{
    "qid": "q_0003", "number": 3, "number_text": "3", "page": 1, "column": 0,
    "bbox_px": [60.1, 900.0, 640.0, 1530.0],
    "text": "Two discs of same mass ... The ratio is",
    "correct_option": null, "answer_text": "",
    "question_images": [],
    "options": {
      "A": {"text": "1:2", "image": null, "image_only": false},
      "B": {"text": "", "image_only": true,
            "image": {"asset_id": "q_0003_opt_B", "path": "data/.../assets/q_0003/option_B.png", "page": 1,
                      "bbox_px": [..], "method": "parse_figure", "source_block_ids": ["0/figure-4"],
                      "sha256": "…", "width": 241, "height": 69}},
      "C": {"text": "", "image_only": true, "image": {"method": "repass_figure", "...": "..."}},
      "D": {"text": "2:3", "image": null, "image_only": false}
    },
    "confidence": 0.95, "ocr_confidence": null, "profile": "default",
    "flags": ["opt_C_figure_by_repass"]
  }],
  "page_issues": {}, "run": {"credits": 12.5, "api_calls": {"parse": 2, "extract": 1}, "needs_review": 0}
}
```
`method` ∈ `parse_figure | repass_figure | ink_fallback | math_text_crop | option_region`.
Math options are stored as **LaTeX text (from Extract) and** an image crop under the default policy; set
`OPTION_IMAGE_MODE=visual_only` to get images only for real diagrams.

---------------------------------------------------------------------------------------------------

## 7. Intermediate data structures

* `PageRecord` — page_no, canonical_path, width, height, source (`embedded|render`), profile, rectified, deskew_deg,
  homography, blur, flags.
* `Block` (`structure.py`) — uid `"<batch>/<id>"`, type, global page, **pixel** `box`, md/plain text, markdown `rng`,
  parent/row/col (table cells), atomic words/lines with confidence, watermark flag.
* `Region` (`segment.py`) — number, page, column, box, anchor block, member blocks.
* `RegionLayout` / `OptionRegion` (`options.py`) — stem box, option cells with texts & figures, answer blocks, flags.
* `VisualRef` (`visuals.py`) — box + source ids + method. → `AssetRef` after cropping.
* `QuestionDraft` / `OptionDraft` (`assemble.py`) — everything decided, before any file is written.

---------------------------------------------------------------------------------------------------

## 8. Validation, failures, retries

| Flag | Severity | Meaning |
|---|---|---|
| `too_few_options`, `options_not_sequential`, `no_option_anchors` | error | option structure broken |
| `option_X_empty` | error | option has neither text nor image |
| `figure_double_role` | error | same figure/overlapping boxes used for question and option |
| `numbering_gap` | error | numbers not consecutive / duplicated (restart at 1 allowed) |
| `asset_missing`, `asset_too_small`, `asset_out_of_bounds`, `image_only_option_without_image` | error | asset problems |
| `no_questions_found`, `parse_failed` (page issues) | error | page produced nothing |
| `fewer_than_4_options`, `extract_missing`, `ink_fallback_*`, `*_figure_by_repass`, `unexplained_ink_in_stem`, `stem_possibly_truncated`, `low_ocr_confidence`, `option_order_mismatch`, `options_from_table_cells` | warning | review if confidence low |

**Ladder:** pages with error flags are re-canonicalized with each profile in `LADDER_PROFILES`, re-parsed,
re-drafted; the attempt with the fewest errors wins (rungs stop at 0 errors). `LADDER_MAX_PAGES` bounds spend.
Still failing → `output/review_queue.json` (question ids + flags + page issues) together with overlays.
Network: exponential backoff on 429/5xx/timeouts, honours `Retry-After`, 422 fails fast. All Landing AI responses
are cached in `data/cache/lat/` keyed by content hash, so re-runs cost nothing; **partial (206) results are never cached**.
`confidence` = 1 − 0.25·errors − 0.05·warnings (capped by Verity OCR confidence when available).

---------------------------------------------------------------------------------------------------

## 9. Config reference (`.env`)
Key groups: Landing AI (`LANDING_API_KEY`, `LANDING_BASE_URL`, models) · canonicalization (`RENDER_DPI`,
`PHOTO_RECTIFY`, `NORMALIZE_ILLUMINATION`, `DESKEW`, `BLUR_THRESHOLD`…) · batching (`BATCH_PAGES`, `MAX_UPLOAD_MB`) ·
geometry (`GUTTER_CLUSTER_TOL`, `MIN_ANCHOR_GAP_FRAC`, `MIN_CLUSTER_SIZE`, `LAST_REGION_PAD_FRAC`) ·
visuals (`INK_THRESHOLD`, `INK_MIN_BBOX_FRAC`, `REPASS_MIN_SIDE`, `ASSET_PAD_PX`, `OPTION_IMAGE_MODE`) ·
patterns (`WATERMARK_PATTERNS`, `ANSWER_MARKERS`, `||`-separated regexes) · extras (`QA_VERITY`, `LADDER_*`, `DEBUG_OVERLAYS`).

---------------------------------------------------------------------------------------------------

## 10. Mapping to your MSA "Rotational Dynamics" sample
* Banner "L.No. 1 Rotational Dynamics" sits above the first number → outside every region → ignored.
* Two columns (Q1–5 | Q6–9, then Q10–14 | Q15…) → handled by column clustering; reading order = column-major.
* `10` + `.` on two lines → `\s+` stripped before the number regex.
* Tiny `A/B/C/D` glyphs at each cell's corner → option anchors (resolution matters: use real PDF pages, not screenshots).
* Diagram beside the stem (Q11) → "inside the stem area, outside any option cell" ⇒ question image.
* Figure between stem and options (Q6, Q7) → question image.
* "Correct Option: 13MR²/32" under Q8 → answer line, excluded from options.
* Diagonal "Matrix Science Academy" watermark → pattern filter + >25%-area filter; ink threshold 120 ignores its light gray.
* Q7/Q8 text clipped by the PDF cell borders is **missing in the source**; no parser can recover it
  (`stem_possibly_truncated` only catches obvious cases).
* Stacked fractions are text/LaTeX → text + crop under `visual_or_math`.

---------------------------------------------------------------------------------------------------

## 11. Known limitations (honest list)
1. Never run against the live API yet (section 3 lists what to verify).
2. Questions that **continue onto the next page/column** are not stitched; the fragment gets flags and goes to review.
3. A page with a single numbered question in a column and no numbering continuity may be missed → `no_questions_found`.
4. Option labels must exist as printed glyphs (A/B/C/D, (A), A.). Unlabelled options → table-cell fallback or review.
5. Strongly curved/folded book photos need a dewarping model (not included); partial shadows are handled.
6. Pen marks over options end up inside crops (flagged only if they create unexplained ink).
7. Async Parse Jobs aren't implemented; large documents run as sequential sync batches (rate-limit-aware via retries).
8. Pro has no confidence scores; confidence comes from heuristics + optional Verity pass.

## 12. Tuning guide
* Question numbers missed/false → `GUTTER_CLUSTER_TOL`, `MIN_ANCHOR_GAP_FRAC`, `MIN_CLUSTER_SIZE`.
* Images missed → lower `INK_MIN_BBOX_FRAC`; images invented from smudges → raise it / raise `INK_THRESHOLD` darkness.
* Crops too tight → `ASSET_PAD_PX`. Last option cut → `LAST_REGION_PAD_FRAC`.
* Photo pages crop wrong → `PHOTO_RECTIFY=off`; skewed → ensure `DESKEW=true`.
* Cost control → `BATCH_PAGES`, `LADDER_PROFILES=` (empty disables ladder), `OPTION_IMAGE_MODE=visual_only`.

## 13. Cost model
Per document: 1 parse call per batch (+ repass parse per missed image + ladder re-parses) + 1 extract per batch
(+ optional Verity parse). `run.credits` in the output sums billing reported by the API.

## 14. Extending
New layout → adjust `segment.py` anchors; new answer formats → `ANSWER_MARKERS`; new retry rung → add a profile in
`canonicalize.PROFILES` and list it in `LADDER_PROFILES`; stricter schema → `lat/schemas.py`.

## 15. First real run checklist
1. `python -m pipeline.cli run file.pdf --stop-after canonicalize` → check `canonical/*.png` look upright and sharp.
2. Run on 2 pages. If HTTP 422 mentions an option/model name → fix `.env` / `lat/client.py` (section 3).
3. Open `debug/overlays/*.png`. Verify blue boxes = questions, green = options.
4. Open `lat_raw/parse_*.json`: confirm option labels arrive as separate blocks, and how figures/tables appear.
   If a stacked fraction is a `figure` block rather than text, set `OPTION_IMAGE_MODE=visual_only` or adapt `options.py`.
5. Check `review_queue.json`; tune thresholds (section 12); then run the full document.
