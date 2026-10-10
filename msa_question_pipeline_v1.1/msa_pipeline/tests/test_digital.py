"""Born-digital fast path (PDF text/vector layer) and the one-call mosaic repass used for photo pages."""
import io
import math

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from pipeline.config import Settings
from pipeline.runner import process_document
from pipeline.stages.pdf_layer import page_blocks
from pipeline.stages.visuals import mosaic_repass
from pipeline.utils.geometry import Box


def _hexagon(page, cx, cy, r):
    pts = [pymupdf.Point(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
           for a in range(30, 390, 60)]
    page.draw_polyline(pts, width=1)


def _png(w, h, rect):
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).rectangle(rect, outline="black", width=3)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def make_digital_pdf(path):
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    y = 60
    p.insert_text((40, y), "1. Which of the following is benzene?", fontsize=11)
    p.insert_text((55, y + 18), "a) Ethane", fontsize=11)
    p.insert_text((200, y + 18), "b)", fontsize=11)
    _hexagon(p, 240, y + 30, 18)                                  # option B is a drawn structure
    p.insert_text((330, y + 18), "c) Hexane", fontsize=11)
    p.insert_text((450, y + 18), "d) Propane", fontsize=11)
    y = 160
    p.insert_text((40, y), "2. Pick the right structure", fontsize=11)
    p.insert_text((55, y + 30), "a)", fontsize=11)
    p.insert_image(pymupdf.Rect(75, y + 12, 175, y + 52), stream=_png(200, 80, (20, 10, 180, 70)))
    p.insert_text((330, y + 30), "b) none", fontsize=11)
    p.insert_text((55, y + 75), "c)", fontsize=11)
    p.insert_image(pymupdf.Rect(75, y + 55, 175, y + 95), stream=_png(200, 80, (20, 10, 180, 70)))  # 3pt apart
    p.insert_text((330, y + 75), "d) all", fontsize=11)
    y = 300
    p.insert_text((40, y), "3. Which is correctly matched?", fontsize=11)
    for k in range(3):                                            # a table grid is NOT a figure
        p.draw_line((55, y + 10 + 20 * k), (300, y + 10 + 20 * k))
    for x in (55, 150, 300):
        p.draw_line((x, y + 10), (x, y + 50))
    p.insert_text((55, y + 70), "a) A and B", fontsize=11)
    p.insert_text((200, y + 70), "b) B and C", fontsize=11)
    p.insert_text((330, y + 70), "c) Only C", fontsize=11)
    p.insert_text((450, y + 70), "d) None", fontsize=11)
    p.insert_text((40, 820), "Page | 1", fontsize=9)
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_layer_finds_labels_figures_and_ignores_tables(tmp_path):
    pdf = make_digital_pdf(tmp_path / "d.pdf")
    with pymupdf.open(str(pdf)) as doc:
        W, H = 2481, 3509
        blocks = page_blocks(doc[0], W, H, 1)
    s = W / 595
    labels = [b for b in blocks if b.plain in ("a)", "b)", "c)", "d)")]
    assert len(labels) == 12
    figs = [b.box for b in blocks if b.type == "figure"]
    assert len(figs) == 3, figs                       # hexagon + two adjacent images, no table
    hexa = [f for f in figs if f.contains_point(240 * s, 90 * s)]
    assert len(hexa) == 1
    imgs = [f for f in figs if f.x0 < 100 * s and f.y0 > 160 * s]
    assert len(imgs) == 2 and imgs[0].intersect(imgs[1]) is None     # adjacent images stay separate
    assert not any(b.plain.startswith("Page") for b in blocks)       # running footer dropped


class FakeDigital:
    """Parse returns markdown only (no useful geometry); Extract misses question 2 entirely."""
    def __init__(self):
        self.calls, self.credits, self.cache_hits, self.png_calls = {}, 0.0, 0, 0

    def parse(self, data, filename, model=None, options=None):
        self.calls["parse"] = self.calls.get("parse", 0) + 1
        if filename.endswith(".png"):
            self.png_calls += 1
        md = "1. Which of the following is benzene?\na) Ethane b) c) Hexane d) Propane\n3. Which is correctly matched?"
        return {"markdown": md, "structure": {"type": "document", "children": [
            {"type": "page", "status": "ok", "grounding": {"page": 1}, "children": []}]},
                "metadata": {"failed_pages": []}}

    def extract(self, markdown, schema):
        self.calls["extract"] = self.calls.get("extract", 0) + 1
        qs = [{"number": "1", "stem": "Which of the following is benzene?", "option_a": "Ethane", "option_b": None,
               "option_c": "Hexane", "option_d": "Propane", "correct_option": None},
              {"number": "3", "stem": "Which is correctly matched?", "option_a": "A and B", "option_b": "B and C",
               "option_c": "Only C", "option_d": "None", "correct_option": None}]
        return {"extraction": {"questions": qs}, "extraction_metadata": {"questions": [{} for _ in qs]}}


def test_digital_pages_cost_two_calls_and_keep_dropped_questions(tmp_path):
    cfg = Settings()
    cfg.data_dir, cfg.api_key, cfg.ladder_profiles = tmp_path / "data", "test", []
    cfg.pdf_text = False                              # this test pins the Parse-based flow
    cfg.describe_images = False
    fake = FakeDigital()
    out = process_document(make_digital_pdf(tmp_path / "d.pdf"), cfg, client=fake, doc_id="dig")
    assert fake.calls == {"parse": 1, "extract": 1} and fake.png_calls == 0     # no repass on digital pages
    # with image descriptions on: exactly ONE extra call for the one page that has (undescribed) images
    cfg.describe_images = True
    fake2 = FakeDigital()
    process_document(make_digital_pdf(tmp_path / "d.pdf"), cfg, client=fake2, doc_id="dig2")
    assert fake2.calls == {"parse": 2, "extract": 1} and fake2.png_calls == 1
    qs = {q.number: q for q in out.questions}
    assert sorted(qs) == [1, 2, 3]                      # Q2 kept although Extract did not return it
    assert qs[1].options["B"].image.method == "pdf_figure" and qs[1].options["B"].image_only
    assert qs[1].options["A"].image is None              # text options get no image
    assert qs[2].options["A"].image.method == "pdf_figure" and qs[2].options["C"].image.method == "pdf_figure"
    assert qs[2].options["B"].image is None
    assert qs[2].options["A"].image.sha256 and "extract_missing" in qs[2].flags
    # a table grid is not a figure: it becomes ONE table image of the stem, and no option gets a picture
    assert [a.method for a in qs[3].question_images] == ["table_crop"]
    assert all(o.image is None for o in qs[3].options.values())


class TileFake:
    def __init__(self):
        self.calls = 0

    def parse(self, data, filename, model=None, options=None):
        self.calls += 1
        g = np.array(Image.open(io.BytesIO(data)).convert("L")) < 128
        H, W = g.shape
        rows = np.where(g.any(axis=1))[0]
        bands, start = [], rows[0]
        for a, b in zip(rows, rows[1:]):
            if b - a > 20:
                bands.append((start, a))
                start = b
        bands.append((start, rows[-1]))
        els = []
        for k, (y0, y1) in enumerate(bands):
            cols = np.where(g[y0:y1 + 1].any(axis=0))[0]
            els.append({"id": f"figure-{k}", "type": "figure", "grounding": {"page": 1, "box": {
                "xmin": cols[0] / W, "ymin": y0 / H, "xmax": cols[-1] / W, "ymax": y1 / H}}})
        return {"markdown": "", "structure": {"type": "document", "children": [
            {"type": "page", "grounding": {"page": 1}, "children": els}]}}


def test_mosaic_repass_is_one_call_and_maps_back(tmp_path):
    img = Image.new("RGB", (2000, 2000), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((300, 300, 500, 450), outline="black", width=6)
    d.rectangle((1200, 1400, 1500, 1700), outline="black", width=6)
    fake = TileFake()
    res = mosaic_repass(fake, img, [Box(250, 250, 600, 500), Box(1100, 1300, 1600, 1800)], Settings())
    assert fake.calls == 1
    (a, _, _), = res[0]
    (b, _, _), = res[1]
    assert abs(a.x0 - 297) < 8 and abs(a.y1 - 453) < 8, a
    assert abs(b.x0 - 1197) < 8 and abs(b.y1 - 1703) < 8, b
