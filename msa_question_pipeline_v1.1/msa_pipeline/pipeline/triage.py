"""Free, local page triage (no API calls): what is on each page, which chapter it belongs to, and the answer keys.

Every page is classified from the PDF itself before anything is sent to Landing AI:
  question_digital    real text with option labels             -> PDF-layer geometry (+ Parse/Extract for text)
  question_composite  digital page whose questions are pasted screenshots (text layer = numbers/headers only)
  question_scanned    full-page image (photo/scan), possibly with a thin text layer of question numbers
  answer_key          only an answer-key grid/list (parsed locally, never sent to the API)
  cover | toc | blank | text_other   (title pages, contents, solutions/theory)  -> skipped
Pages can hold questions AND a key (MSA layout): they are question pages and their key is still read.

Answer keys come in three printed forms, all read from text-layer word positions:
  "1) a  2) c ..."  (number left of letter)   |   "Q. NO 1 2 3 ... / ANS A B C ..."  |  "ANSWER KEY" grid
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pymupdf

KEY_MARK = re.compile(r"answer\s*key|q\s*\.\s*no\b", re.I)
LABEL_LINE = re.compile(r"^\s*(?:\(?[a-dA-D][).]|[A-D])(?:\s|$)")
NUM_LINE = re.compile(r"^\s*(?:\(?(\d{1,3})[).]|(\d{1,3}))\s*(?:$|[^\d\s.,/])")
TOC_LINE = re.compile(r"(\.{4,}|…{2,})\s*\d+\s*$|\s\d{1,3}\s*(?:to|-)\s*\d{1,3}\s*$")
CHAPTER_PATTERNS = [
    re.compile(r"^\s*L\.?\s*No\.?\s*(\d{1,2})\s+(.{3,60})$", re.I),                 # "L.No. 1 Rotational Dynamics"
    re.compile(r"^\s*(\d{1,2})\s*[-–.]\s*([A-Za-z][A-Za-z ,&'()/-]{2,60})$"),        # "1-ROTATIONAL DYNAMICS"
]
BRAND = re.compile(r"impulse\s*batch|matrix\s*science|rabhchemia|active\s*site|page\s*\|", re.I)


@dataclass
class PageInfo:
    page: int
    kind: str
    chapter: Optional[str] = None            # title printed on this page (None -> inherits)
    numbers: list[int] = field(default_factory=list)
    key: dict[int, str] = field(default_factory=dict)
    text_chars: int = 0
    image_frac: float = 0.0

    @property
    def is_question(self) -> bool:
        return self.kind.startswith("question")


@dataclass
class DocTriage:
    pages: dict[int, PageInfo]
    chapter_of_page: dict[int, str]
    keys: dict[str, dict[int, str]]          # chapter -> {question number: letter}

    def question_pages(self) -> list[int]:
        return [p for p, i in sorted(self.pages.items()) if i.is_question]

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for i in self.pages.values():
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
        return {"pages": len(self.pages), "kinds": kinds, "chapters": len(set(self.chapter_of_page.values())),
                "key_entries": sum(len(k) for k in self.keys.values())}


def _rows(words: list[tuple], tol: float) -> list[list[tuple]]:
    rows: list[list[tuple]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if rows and abs(cy - (rows[-1][0][1] + rows[-1][0][3]) / 2) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w[0]) for r in rows]


_KNUM = re.compile(r"^\(?(\d{1,3})[).]?$")
_KLET = re.compile(r"^\(?([A-Da-d])\)?$")


def read_answer_key(page: pymupdf.Page) -> dict[int, str]:
    """Number -> letter pairs below the first key marker. A letter belongs to the nearest number either directly
    to its LEFT on the same row ("12) d") or directly ABOVE in the same column (Q.NO row over ANS row)."""
    words = page.get_text("words")
    marks = [w for w in words if KEY_MARK.search(w[4])]
    if not marks:                                   # "Q." and "NO" are separate words: find the marker per line
        marks = [(ln["bbox"][0], ln["bbox"][1], ln["bbox"][2], ln["bbox"][3], "")
                 for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", [])
                 if KEY_MARK.search(" ".join(sp["text"] for sp in ln["spans"]))]
    if not marks:
        text = page.get_text()
        if not KEY_MARK.search(text):
            return {}
        top = 0.0
    else:
        top = min(w[1] for w in marks) - 2
    toks = [w for w in words if w[1] >= top]
    nums = [(int(m.group(1)), w) for w in toks if (m := _KNUM.match(w[4]))]
    lets = [(m.group(1).upper(), w) for w in toks if (m := _KLET.match(w[4]))]
    if len(nums) < 3 or len(lets) < 3:
        return {}
    h = sorted(w[3] - w[1] for _, w in nums)[len(nums) // 2]
    key: dict[int, str] = {}
    used: set[int] = set()
    for L, lw in sorted(lets, key=lambda t: (t[1][1], t[1][0])):
        lcx, lcy = (lw[0] + lw[2]) / 2, (lw[1] + lw[3]) / 2
        best, bd = None, 1e9
        for n, nw in nums:
            if id(nw) in used:
                continue
            ncx, ncy = (nw[0] + nw[2]) / 2, (nw[1] + nw[3]) / 2
            if abs(ncy - lcy) <= 0.6 * h and nw[2] <= lw[0] + 2 and lw[0] - nw[2] < 6 * h:   # "12) d"
                d = lw[0] - nw[2]
            elif abs(ncx - lcx) <= 2.5 * h and 0 < lcy - ncy < 4 * h:                        # grid column
                d = (lcy - ncy) + abs(ncx - lcx)
            else:
                continue
            if d < bd:
                best, bd = (n, nw), d
        if best is not None and best[0] not in key:
            key[best[0]] = L
            used.add(id(best[1]))
    return key if len(key) >= 3 else {}


def _chapter_title(page: pymupdf.Page) -> Optional[str]:
    H = page.rect.height
    blocks = page.get_text("dict")["blocks"]
    sizes = sorted(sp["size"] for b in blocks for ln in b.get("lines", []) for sp in ln["spans"] if sp["text"].strip())
    body = sizes[len(sizes) // 2] if sizes else 0
    for b in blocks:
        for ln in b.get("lines", []):
            if ln["bbox"][1] > 0.16 * H:
                continue
            if body and max((sp["size"] for sp in ln["spans"]), default=0) < 1.15 * body:
                continue                                  # not a heading (e.g. "1. NaOH ..." in a solution)
            t = " ".join(s["text"] for s in ln["spans"]).strip()
            if not t or BRAND.search(t):
                continue
            for pat in CHAPTER_PATTERNS:
                m = pat.match(t)
                if m:
                    title = re.sub(r"\s+", " ", m.group(2)).strip().title()
                    return f"{int(m.group(1))}. {title}"
    return None


def classify_page(page: pymupdf.Page, page_no: int) -> PageInfo:
    W, H = page.rect.width, page.rect.height
    text = page.get_text()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    imgs = page.get_image_info()
    img_frac = sum(max(0, i["bbox"][2] - i["bbox"][0]) * max(0, i["bbox"][3] - i["bbox"][1]) for i in imgs) / (W * H)
    full = any((i["bbox"][2] - i["bbox"][0]) > 0.8 * W and (i["bbox"][3] - i["bbox"][1]) > 0.8 * H for i in imgs)
    content = [ln for ln in lines if not BRAND.search(ln)]
    chars = sum(len(ln.strip()) for ln in content)
    info = PageInfo(page=page_no, kind="text_other", chapter=_chapter_title(page), text_chars=chars,
                    image_frac=round(img_frac, 3))
    info.key = read_answer_key(page)
    # question numbers above any key marker
    key_top = min((ln["bbox"][1] for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", [])
                   if KEY_MARK.search(" ".join(sp["text"] for sp in ln["spans"]))), default=H + 1)
    nums = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            if ln["bbox"][1] >= key_top:
                continue
            t = " ".join(s["text"] for s in ln["spans"]).strip()
            m = NUM_LINE.match(t)
            if m and ln["bbox"][0] < 0.62 * W:
                nums.append(int(m.group(1) or m.group(2)))
    info.numbers = nums
    # option labels count only ABOVE an answer key: the key's "A B C D" row is not a question's options
    above = [" ".join(sp["text"] for sp in ln["spans"]) for blk in page.get_text("dict")["blocks"]
             for ln in blk.get("lines", []) if ln["bbox"][1] < key_top]
    letters = [m.group(0).strip().strip("().").lower() for ln in above
               if (m := LABEL_LINE.match(ln)) and not BRAND.search(ln)]
    # real options come as ordered runs a, b, c, d; solution pages only have scattered answer letters
    labels = sum(1 for i in range(len(letters) - 3) if letters[i:i + 4] == ["a", "b", "c", "d"])
    words_frac = sum((w[2] - w[0]) * (w[3] - w[1]) for w in page.get_text("words") if w[1] < key_top) / (W * H)
    tocish = sum(1 for ln in content if TOC_LINE.search(ln))
    if chars < 30 and img_frac < 0.05:
        info.kind = "blank"
    elif tocish >= 5:
        info.kind = "toc"
    elif info.key and len(nums) < 2:
        info.kind = "answer_key"
    elif full:
        info.kind = "question_scanned" if (nums or page_no > 1) else "cover"
    elif labels >= 1:
        info.kind = "question_digital"
    elif img_frac > 0.12 and words_frac < 0.05:
        info.kind = "question_composite" if (nums or page_no > 2) else "cover"
    return info


def triage(pdf_path) -> DocTriage:
    pages: dict[int, PageInfo] = {}
    with pymupdf.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc):
            pages[i + 1] = classify_page(page, i + 1)
    # a "cover" is only trusted when the document is clearly not a scan; in doubt a page is parsed (skipping a real
    # question page costs correctness, parsing one cover costs a few credits)
    scanned = sum(1 for i in pages.values() if i.kind == "question_scanned")
    if len(pages) <= 3 or scanned >= 0.5 * len(pages):
        for i in pages.values():
            if i.kind == "cover":
                i.kind = "question_scanned" if i.image_frac > 0.5 else "question_composite"
    chapter_of_page: dict[int, str] = {}
    cur, anon = None, 0
    last_num = None
    for p in sorted(pages):
        info = pages[p]
        if info.chapter:
            if info.chapter != cur:
                last_num = None
            cur = info.chapter
        elif info.is_question and info.numbers and last_num is not None and min(info.numbers) == 1 \
                and last_num > 5:                     # numbering restarted without a printed title
            anon += 1
            cur = f"part {anon + 1}"
        if cur is None:
            cur = "part 1"
        chapter_of_page[p] = cur
        if info.is_question and info.numbers:
            last_num = max(info.numbers)
    keys: dict[str, dict[int, str]] = {}
    for p in sorted(pages):
        if pages[p].key:
            keys.setdefault(chapter_of_page[p], {}).update(pages[p].key)
    return DocTriage(pages, chapter_of_page, keys)
