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


def test_shared_line_option_ranges_are_narrowed():
    # the live Extract gives every option the range of the whole "a) .. b) .. c) .. d) .." line
    from pipeline.stages.semantics import narrow_option_span
    md = "Q1 a) C<sub>2</sub>H<sub>5</sub>OH  b) CH<sub>3</sub>CHO  c) 12  d) 12\n"
    s, e = 3, len(md) - 1
    got = {L: md[slice(*narrow_option_span(md, s, e, L, v))] for L, v in
           (("a", "C<sub>2</sub>H<sub>5</sub>OH"), ("b", "CH<sub>3</sub>CHO"), ("c", "12"), ("d", "12"))}
    assert got == {"a": "C<sub>2</sub>H<sub>5</sub>OH", "b": "CH<sub>3</sub>CHO", "c": "12", "d": "12"}
    assert narrow_option_span(md, s, e, "d", "not there") == (s, e)


def test_infer_cells_never_returns_inverted_boxes():
    from pipeline.stages.options import infer_cells
    region = Box(0, 870, 2481, 1197)
    same = Box(224, 849, 1945, 912)                            # all options grounded to the same line
    for known in ({L: same for L in "ABCD"},
                  {"A": Box(227, 973, 789, 1032), "B": Box(142, 1035, 1818, 1808),
                   "C": Box(229, 1097, 1337, 1157), "D": Box(228, 1158, 566, 1217)}):
        notes: list[str] = []
        cells = infer_cells(known, region, 3509, notes)
        assert cells and all(c.x1 > c.x0 and c.y1 > c.y0 for c in cells.values()), cells
        assert notes == ["option_cells_equal_split"]
