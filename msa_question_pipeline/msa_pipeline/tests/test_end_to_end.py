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
    return c


def test_full_pipeline(tmp_path, cfg):
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
            assert Path(a.path).exists() and a.width > 20
    assert (cfg.data_dir / "t1" / "output" / "questions.json").exists()
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
