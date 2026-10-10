"""Cost cuts: local split of page text (instead of Extract) and page text from the PDF itself (instead of Parse)."""
import pymupdf

from pipeline.stages.local_extract import local_extract, validate
from pipeline.stages.pdf_text import build_pdf_batch
from tests.test_digital import make_digital_pdf


def test_local_split_lines_inline_labels_and_table_cells():
    md = ("1 What is X?\n [ ] A) 14\n [ ] B) 12\n [ ] C) 7\n [ ] D) 10\n\n"
          "2 Which is right?\nA) 1 N C) 4 N\nB) 8 N D) 2 N\n\n"
          "<table>\n<tr><td>3</td><td>Ratio b/R is</td></tr>\n<tr><td>A</td><td>1:1</td><td>B</td><td>2:1</td></tr>\n"
          "<tr><td>C</td><td>3:1</td><td>D</td><td>4:1</td></tr>\n</table>\n")
    ext = local_extract(md)
    qs = {q["number"]: q for q in ext["extraction"]["questions"]}
    assert [qs["1"][f"option_{L}"] for L in "abcd"] == ["14", "12", "7", "10"]
    assert [qs["2"][f"option_{L}"] for L in "abcd"] == ["1 N", "8 N", "4 N", "2 N"]
    assert [qs["3"][f"option_{L}"] for L in "abcd"] == ["1:1", "2:1", "3:1", "4:1"]
    assert validate(ext, {1, 2, 3}) is None
    for q, m in zip(ext["extraction"]["questions"], ext["extraction_metadata"]["questions"]):
        for L in "abcd":                             # every value points at its own text in the markdown
            r = m[f"option_{L}"]["ranges"][0]
            assert md[r["start"]:r["end"]] == q[f"option_{L}"]


def test_local_split_rejects_what_it_cannot_prove():
    ext = local_extract("1 Which?\nA) x\nB)\nC) z\nD) w\n2 Next?\nA) p\nB) q\nC) r\nD) s\n")
    assert "option B empty" in validate(ext, {1, 2})
    assert "not found" in validate(local_extract("1 Which?\nA) x\nB) y\nC) z\nD) w\n"), {1, 2})


def test_pdf_text_ranges_point_at_their_text(tmp_path):
    pdf = make_digital_pdf(tmp_path / "d.pdf")
    br, unsafe = build_pdf_batch(pdf, [1], {1: (2481, 3509)}, 1)
    assert unsafe == [] and br.page_nums == [1]
    for b in br.blocks:
        if b.type == "text" and b.rng[0] >= 0:
            got = br.markdown[b.rng[0]:b.rng[1]]
            assert got == b.plain.strip() or got.endswith(")"), (b.plain, got)
    qs = {q["number"]: q for q in local_extract(br.markdown)["extraction"]["questions"]}
    assert qs["1"]["option_a"] == "Ethane" and qs["3"]["option_d"] == "None"
