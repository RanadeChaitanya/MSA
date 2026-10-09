"""v2 output contract: document.json + assets/ + image_descriptions.txt are complete, consistent, rebuildable."""
import json
import shutil
from pathlib import Path

import pymupdf
import pytest

from pipeline.config import Settings
from pipeline.export import compose, commit, manifest_text
from pipeline.reconstruct import load_document, reconstruct, to_markdown, verify
from pipeline.runner import process_document
from pipeline.schema import Document, ImageComponent, json_schema
from tests.fake_lat import FakeLat
from tests.make_pdf import make_photo_pdf
from tests.test_digital import FakeDigital, _png, make_digital_pdf

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path):
    c = Settings()
    c.data_dir, c.api_key, c.ladder_profiles = tmp_path / "data", "test", []
    c.option_image_mode = "visual_or_math"
    return c


def _run(tmp_path, doc_id="v2"):
    cfg = _cfg(tmp_path)
    pdf = tmp_path / "paper.pdf"
    if not pdf.exists():
        make_photo_pdf(pdf)
    process_document(pdf, cfg, client=FakeLat(), doc_id=doc_id)
    return cfg.data_dir / doc_id


def test_every_question_rebuilds_and_every_image_resolves(tmp_path):
    d = _run(tmp_path)
    doc = load_document(d)                                     # strict schema validation
    assert verify(doc, d) == []
    owners = 0
    for q in doc.questions:
        rq = reconstruct(doc, q.qid, d)
        assert [o["label"] for o in rq["options"]] == sorted(o["label"] for o in rq["options"])
        for where, comps in [("stem", rq["stem"])] + [(o["label"], o["content"]) for o in rq["options"]]:
            for c in comps:
                if c["type"] == "image":
                    a = doc.assets[c["asset_id"]]
                    assert Path(c["file"]).is_file()
                    assert (a.owner.qid, a.owner.role, a.owner.option) == \
                        (q.qid, "stem" if where == "stem" else "option", None if where == "stem" else where)
                    owners += 1
        assert to_markdown(rq) == to_markdown(reconstruct(doc, q.qid, d))      # deterministic
    assert owners == len(doc.assets) > 0
    man = (d / "image_descriptions.txt").read_text(encoding="utf-8")
    assert man == manifest_text(doc)
    assert man.count("=== end ===") == len(doc.assets)
    for aid in doc.assets:
        assert man.count(f"=== {aid} ===") == 1


def test_output_holds_no_machine_paths_or_raw_extraction_fields(tmp_path):
    d = _run(tmp_path)
    raw = (d / "document.json").read_text(encoding="utf-8")
    import re
    assert not re.search(r'[A-Za-z]:\\|/home/|/Users/', raw)                # no absolute machine paths
    for banned in (str(tmp_path).replace("\\", "\\\\"), "source_block_ids", "bbox_px", "canonical_path", "profile", "markdown",
                   "extraction_metadata", "grounding"):
        assert banned not in raw, banned
    assert not (d / "output" / "questions.json").exists() and not (d / "output" / "questions_slim.json").exists()


def test_reprocessing_is_idempotent(tmp_path):
    d = _run(tmp_path)
    first = (d / "document.json").read_bytes()
    files1 = sorted(p.relative_to(d).as_posix() for p in (d / "assets").rglob("*.png"))
    d2 = _run(tmp_path)
    assert d2 == d and (d / "document.json").read_bytes() == first
    assert sorted(p.relative_to(d).as_posix() for p in (d / "assets").rglob("*.png")) == files1
    assert not (d / "assets.staging").exists() and not (d / "assets.old").exists()


def test_leftovers_of_an_interrupted_run_are_cleaned(tmp_path):
    d = _run(tmp_path)
    (d / "assets.staging" / "junk").mkdir(parents=True)
    (d / "assets.staging" / "junk" / "x.png").write_bytes(b"x")
    (d / "assets" / "stray.png").write_bytes(b"x")                 # orphan from some older run
    _run(tmp_path)
    assert not (d / "assets.staging").exists()
    assert verify(load_document(d), d) == []                       # stray file gone with the old assets dir


def test_commit_refuses_inconsistent_output_and_keeps_previous(tmp_path):
    d = _run(tmp_path)
    before = (d / "document.json").read_bytes()
    doc = load_document(d)
    aid = next(iter(doc.assets))
    staging = d / "assets.staging"
    shutil.copytree(d / "assets", staging)
    (staging / doc.assets[aid].path[len("assets/"):]).unlink()   # a crop that failed to write
    with pytest.raises(RuntimeError, match="file missing"):
        commit(d, doc, {})
    assert (d / "document.json").read_bytes() == before and verify(load_document(d), d) == []


def test_verify_detects_tampering_and_wrong_owner(tmp_path):
    d = _run(tmp_path)
    doc = load_document(d)
    aid, a = next(iter(doc.assets.items()))
    (d / a.path).write_bytes(b"not the original image")
    assert any("sha256 mismatch" in p for p in verify(doc, d))
    bad = doc.model_copy(deep=True)
    bad.assets[aid].owner.qid = "p9999-q999"
    assert any("owner" in p for p in verify(bad, d))
    j = json.loads((d / "document.json").read_text(encoding="utf-8"))
    j["questions"][0]["extraction"] = {"raw": 1}                     # unknown fields are rejected
    with pytest.raises(Exception):
        Document.model_validate(j)


def test_compose_keeps_order_and_never_splits_math_or_tables():
    cs = compose("Find X. $a + b$ is shown <table><tr><td>1 2</td></tr></table> then $$x^2$$ end",
                 [(0.0, "img0"), (0.5, "img1"), (1.0, "img2")])
    kinds = [c.type for c in cs]
    assert kinds[0] == "image" and kinds[-1] == "image" and kinds.count("image") == 3
    assert "table" in kinds and "equation" in kinds
    texts = " ".join(c.text for c in cs if c.type == "text")
    assert "$a + b$" in texts                                        # inline math intact
    assert [c.asset_id for c in cs if isinstance(c, ImageComponent)] == ["img0", "img1", "img2"]


def test_option_with_two_images_keeps_both_in_order(tmp_path):
    pdf = tmp_path / "two.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((40, 60), "1. Which of the following is benzene?", fontsize=11)
    p.insert_text((55, 100), "a)", fontsize=11)
    p.insert_image(pymupdf.Rect(75, 80, 150, 120), stream=_png(150, 80, (20, 10, 130, 70)))
    p.insert_image(pymupdf.Rect(160, 80, 235, 120), stream=_png(150, 80, (20, 10, 130, 70)))
    p.insert_text((330, 100), "b) Ethane", fontsize=11)
    p.insert_text((55, 150), "c) Hexane", fontsize=11)
    p.insert_text((330, 150), "d) Propane", fontsize=11)
    doc.save(str(pdf))
    cfg = _cfg(tmp_path)
    class OneQ(FakeDigital):
        def extract(self, markdown, schema):
            qs = [{"number": "1", "stem": "Which of the following is benzene?", "option_a": None,
                   "option_b": "Ethane", "option_c": "Hexane", "option_d": "Propane", "correct_option": None}]
            return {"extraction": {"questions": qs}, "extraction_metadata": {"questions": [{}]}}
    process_document(pdf, cfg, client=OneQ(), doc_id="two")
    d = cfg.data_dir / "two"
    out = load_document(d)
    q1 = next(q for q in out.questions if q.number == 1)
    a = next(o for o in q1.options if o.label == "A")
    ids = [c.asset_id for c in a.content if isinstance(c, ImageComponent)]
    assert len(ids) == 2
    xs = [out.assets[i].bbox[0] for i in ids]
    assert xs == sorted(xs)                                          # left image first
    assert verify(out, d) == []


def test_published_json_schema_is_current():
    published = json.loads((ROOT / "schemas" / "document.schema.json").read_text(encoding="utf-8"))
    assert published == json_schema(), "run: python -m pipeline.cli schema > schemas/document.schema.json"
