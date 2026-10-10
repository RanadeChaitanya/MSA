from pathlib import Path

import pytest

from pipeline.config import Settings
from pipeline.runner import process_document
from tests.fake_lat import FakeLat
from tests.make_pdf import make_photo_pdf


@pytest.fixture()
def cfg(tmp_path):
    c = Settings()
    c.data_dir = tmp_path / "data"
    c.api_key = "test"
    c.debug_overlays = True
    c.ladder_profiles = []          # keep the first test deterministic
    c.extract_mode = "extract"      # these tests exercise how Landing AI Extract output is handled
    return c


def test_full_pipeline(tmp_path, cfg):
    cfg.option_image_mode = "visual_or_math"       # complex math (\\sqrt, \\frac) becomes an image; plain text does not
    pdf = make_photo_pdf(tmp_path / "paper.pdf")
    fake = FakeLat()
    out = process_document(pdf, cfg, client=fake, doc_id="t1")
    qs = {q.number: q for q in out.questions}
    assert sorted(qs) == [1, 2, 3, 4], [q.number for q in out.questions]       # digit noise ignored, banner ignored
    # option labels + text from Extract
    assert set(qs[1].options) == {"A", "B", "C", "D"}
    assert qs[1].options["B"].text.startswith("$\\sqrt")
    # Q2: question image from parse figure inside stem region
    assert len(qs[2].question_images) == 1 and qs[2].question_images[0].method == "parse_figure"
    # Q3: B = parse figure (image-only), C = missed by parse -> residual ink -> repass
    assert qs[3].options["B"].image and qs[3].options["B"].image.method == "parse_figure"
    assert qs[3].options["B"].image_only
    c = qs[3].options["C"]
    assert c.image is not None and c.image_only and c.image.method == "repass_figure"
    assert fake.repass_calls >= 1
    # math options get an image under the default policy visual_or_math
    assert qs[1].options["B"].image and qs[1].options["B"].image.method == "math_text_crop"
    assert qs[1].options["A"].image is None            # plain text option stays text-only
    # Q4: answer line captured and removed from options
    assert qs[4].correct_option == "A" and "Correct Option" in qs[4].answer_text
    # no figure appears in two roles, every asset exists
    for q in out.questions:
        assert "figure_double_role" not in q.flags
        for a in list(q.question_images) + [o.image for o in q.options.values() if o.image]:
            assert not Path(a.path).is_absolute() and "\\" not in a.path       # relative, posix
            assert (cfg.data_dir / "t1" / a.path).exists() and a.width > 20
    assert (cfg.data_dir / "t1" / "document.json").exists() and (cfg.data_dir / "t1" / "image_descriptions.txt").exists()
    assert out.run["needs_review"] == 0, out.run


def test_validation_flags_missing_options(tmp_path, cfg):
    pdf = make_photo_pdf(tmp_path / "paper2.pdf")
    fake = FakeLat()
    # remove option D label + text of Q1 to simulate a bad parse
    q1 = fake.questions[0]
    for bid in q1["opts"]["D"]:
        fake.blocks[:] = [b for b in fake.blocks if b["id"] != bid]
    q1["opts"]["D"] = []
    q1["opt_text"]["D"] = ""
    out = process_document(pdf, cfg, client=fake, doc_id="t2")
    q = next(x for x in out.questions if x.number == 1)
    assert any(f.startswith("option_D") or f == "fewer_than_4_options" for f in q.flags), q.flags


def test_skewed_photo_is_deskewed(tmp_path, cfg):
    from pipeline.stages.canonicalize import canonicalize_pdf
    pdf = make_photo_pdf(tmp_path / "skew.pdf", skew_deg=2.5)
    # ruling lines are needed for skew estimation; make_pdf draws them
    recs = canonicalize_pdf(pdf, tmp_path / "canon", cfg)
    assert recs[0].source == "embedded"
    assert abs(recs[0].deskew_deg) > 1.0


def test_opaque_table_layout_keeps_every_extracted_question(tmp_path, cfg):
    """Regression for the first real run: Extract found questions but anchor segmentation found none,
    and the old code dropped them (8 kept of ~245)."""
    from tests.fake_lat import FakeOpaque
    from tests.make_pdf import make_opaque_pdf
    cfg.option_image_mode = "visual_or_math"
    pdf = make_opaque_pdf(tmp_path / "opaque.pdf")
    fake = FakeOpaque()
    out = process_document(pdf, cfg, client=fake, doc_id="t_opaque")
    assert [q.number for q in out.questions] == [1, 2, 3]
    for q in out.questions:
        assert "region_from_spans" in q.flags and "layout_from_spans" in q.flags
        assert set(q.options) == {"A", "B", "C", "D"}
        assert not q.question_images, "stem text must never become a 'question image'"
        assert not any("ink_fallback" in f for f in q.flags), q.flags
    q2 = out.questions[1]
    assert q2.options["D"].image_only and q2.options["D"].image.method == "repass_figure"
    assert q2.options["A"].text == "7/2" and q2.options["A"].image is None
    # math option text gets a crop (visual_or_math) and lives in its own grid cell
    q1 = out.questions[0]
    assert q1.options["D"].image is not None and q1.options["D"].image.method == "math_text_crop"   # $\\sqrt{3}:1$
    assert q1.options["A"].image is None and q1.options["B"].image is None                          # plain text: no crop


def test_shared_line_is_sliced_proportionally():
    from pipeline.stages.parse_stage import BatchResult
    from pipeline.stages.semantics import ground_span
    from pipeline.stages.structure import Block
    from pipeline.utils.geometry import Box
    md = "A) 1:1    B) 1:2"
    blk = Block(uid="0/t", id="t", type="table", page=1, local_page=1, box=Box(0, 0, 1600, 40), md=md, plain=md,
                rng=(0, len(md)), atomic=[{"box": Box(0, 0, 1600, 40), "conf": None, "rng": (0, len(md))}])
    br = BatchResult(batch_id=0, page_nums=[1], markdown=md, structure={}, metadata={}, blocks=[blk])
    a = ground_span(br, 3, 6)[0]
    b = ground_span(br, 13, 16)[0]
    assert a.sliced and b.sliced and a.box.x1 < b.box.x0


# ----------------------------------------------------------------------------------------------
# Regression tests for the problems found in the second real run
# ----------------------------------------------------------------------------------------------
def _run(tmp_path, cfg, fake, name):
    from tests.make_pdf import make_opaque_pdf
    return process_document(make_opaque_pdf(tmp_path / f"{name}.pdf", fake), cfg, client=fake, doc_id=name)


def test_default_policy_does_not_crop_readable_text(tmp_path, cfg):
    """CH3COOH / sp3 style options must stay text (default OPTION_IMAGE_MODE=visual_only)."""
    from tests.fake_lat import FakeLat
    assert cfg.option_image_mode == "visual_only"
    out = process_document(make_photo_pdf(tmp_path / "p.pdf"), cfg, client=FakeLat(), doc_id="pol")
    q1 = next(q for q in out.questions if q.number == 1)
    assert all(o.image is None for o in q1.options.values()), "plain/sqrt text must not be cropped by default"
    from pipeline.stages.visuals import MATH_RE
    for t in ["CH<sub>3</sub>COOH", "sp3", "$C_2H_4$", "x^2", "(CH<sub>3</sub>)<sub>2</sub>CHOH"]:
        assert not MATH_RE.search(t), t
    for t in ["$\\frac{7}{2}MR^2$", "$C_2H_4$ $\\xrightarrow{HI}$ A", "$$x$$"]:
        assert MATH_RE.search(t), t


def test_options_sharing_a_line_get_distinct_crops(tmp_path, cfg):
    from tests.fake_lat import FakeSharedLines
    cfg.option_image_mode = "always"
    out = _run(tmp_path, cfg, FakeSharedLines(), "shared")
    for q in out.questions:
        imgs = {L: o.image for L, o in q.options.items() if o.image}
        if len(imgs) < 4:
            continue
        assert len({a.sha256 for a in imgs.values()}) == 4, f"{q.qid}: duplicate crops"
        assert len({tuple(a.bbox_px) for a in imgs.values()}) == 4
        assert imgs["A"].bbox_px[2] <= imgs["B"].bbox_px[0] + 1          # A left of B
        assert imgs["C"].bbox_px[2] <= imgs["D"].bbox_px[0] + 1
        assert "figure_double_role" not in q.flags
    assert any(len({o.image.sha256 for o in q.options.values() if o.image}) == 4 for q in out.questions)


def test_text_match_grounds_items_whose_spans_are_missing(tmp_path, cfg):
    from tests.fake_lat import FakeNoSpans
    out = _run(tmp_path, cfg, FakeNoSpans(), "nospans")
    assert [q.number for q in out.questions] == [1, 2, 3]
    for q in out.questions:
        assert q.bbox_px is not None and q.bbox_px != [0.0, 0.0, 1.0, 1.0]
        assert "grounded_by_text_match" in q.flags and "no_geometry" not in q.flags
        assert set(q.options) == {"A", "B", "C", "D"}
    assert out.questions[1].options["D"].image_only          # image-only option still found via repass
    assert "numbering_gap" not in " ".join(f for q in out.questions for f in q.flags)


def test_ungroundable_question_keeps_order_page_and_no_fake_bbox(tmp_path, cfg):
    from tests.fake_lat import FakeGhost
    out = _run(tmp_path, cfg, FakeGhost(), "ghost")
    assert [q.number for q in out.questions] == [1, 99, 2, 3]      # Extract's reading order is preserved
    g = out.questions[1]
    assert g.bbox_px is None and g.page == 1
    assert "no_geometry" in g.flags and "page_inferred" in g.flags
    assert not any(f.endswith("_without_region") for f in g.flags)   # no 4x redundant penalties
    assert set(g.options) == {"A", "B", "C"}                          # option D was null in Extract
    assert out.questions[0].bbox_px is not None


def test_big_text_block_containing_watermark_phrase_is_not_discarded():
    from pipeline.config import Settings
    from pipeline.stages.structure import Block, mark_watermarks
    from pipeline.utils.geometry import Box
    long_txt = "Q1 " + "An organic compound X on treatment with PCC gives Y. " * 10 + " Matrix Science Academy"
    big = Block(uid="0/t1", id="t1", type="text", page=1, local_page=1, box=Box(0, 0, 2000, 3000), md=long_txt,
                plain=long_txt)
    wm = Block(uid="0/t2", id="t2", type="text", page=1, local_page=1, box=Box(100, 100, 1500, 900),
               md="Matrix Science Academy", plain="Matrix Science Academy")
    mark_watermarks([big, wm], Settings(), 2481, 3509)
    assert not big.watermark and wm.watermark


def test_null_option_with_known_cell_becomes_image_only_crop(tmp_path, cfg):
    from tests.fake_lat import FakeNullOptionD
    assert cfg.option_image_mode == "visual_only"
    out = _run(tmp_path, cfg, FakeNullOptionD(), "nulld")
    d = out.questions[2].options["D"]
    assert d.text == "" and d.image_only and d.image is not None and d.image.method == "option_region"
    assert "option_D_empty" not in out.questions[2].flags and "too_few_options" not in out.questions[2].flags
