import pytest

from pipeline.config import Settings
from pipeline.lat.client import LatClient, LatError
from pipeline.retry.ladder import run_ladder
from pipeline.utils.geometry import Box


def test_placeholder_key_is_rejected(tmp_path):
    c = Settings()
    c.data_dir = tmp_path
    with pytest.raises(LatError):
        LatClient(c)          # api_key == APIKEYHERE


def test_box_math():
    a, b = Box(0, 0, 10, 10), Box(5, 5, 15, 15)
    assert a.intersect(b) == Box(5, 5, 10, 10)
    assert a.overlap_ratio(b) == 0.25
    assert Box.union_all([a, b]) == Box(0, 0, 15, 15)


def test_ladder_keeps_best_attempt():
    cur = {1: (["bad"], ["x"], "rec0")}
    scores = {"bad": 3, "mid": 2, "good": 0}
    seq = iter([("mid", [], "rec1"), ("good", [], "rec2")])
    # drafts are plain strings here; score_fn maps them to error counts
    res = run_ladder([1], ["p1", "p2"], lambda p, prof: ([next(seq)[0]], [], prof),
                     lambda d, i: scores[d[0]], cur)
    assert res[1][0] == ["good"] and res[1][2] == "p2"


def test_ladder_survives_exception():
    cur = {1: (["bad"], [], "rec0")}
    def boom(p, prof):
        raise RuntimeError("x")
    res = run_ladder([1], ["p1"], boom, lambda d, i: 1, cur)
    assert res[1][2] == "rec0"
