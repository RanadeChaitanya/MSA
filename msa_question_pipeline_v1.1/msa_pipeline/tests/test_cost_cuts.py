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


def test_local_split_value_printed_before_its_bare_label():
    md = ("1 Truth values of p and q are\nT, T\n [ ] A)\n [ ] B) F, T\n [ ] C) T, F\nF, F\n [ ] D)\n\n"
          "2 Volume occupied in bcc?\n74%\n [ ] A)\n52.36%\n [ ] B)\n68%\n [ ] C)\n80%\n [ ] D)\n\n"
          "3 Which?\n A)\n B) x\n C) y\n D) z\n")
    qs = {q["number"]: q for q in local_extract(md)["extraction"]["questions"]}
    assert [qs["1"][f"option_{L}"] for L in "abcd"] == ["T, T", "F, T", "T, F", "F, F"]
    assert [qs["2"][f"option_{L}"] for L in "abcd"] == ["74%", "52.36%", "68%", "80%"]
    assert qs["3"]["stem"] == "Which?" and qs["3"]["option_a"] == ""      # the stem is never taken as an option


def test_local_split_rejects_ocr_debris():
    ok = "2 Next?\n A) p\n B) q\n C) r\n D) s\n"
    assert "empty" in validate(local_extract("1 Which?\n [x] A) _\n [ ] B) F, T\n C) x\n D) y\n" + ok), {1, 2})
    assert "debris" in validate(local_extract("1 Which?\n A) p\n B) F, T [x]\n C) x\n D) y\n" + ok), {1, 2})


def test_local_split_debris_after_bare_label_takes_value_before():
    md = "1 If r is false, p and q are\nT , F\n [x] A) _\n [ ] B) F , F\n [ ] C) F , T\n [ ] D) T , T\n2 Next?\n A) p\n B) q\n C) r\n D) s\n"
    q = local_extract(md)["extraction"]["questions"][0]
    assert [q[f"option_{L}"] for L in "abcd"] == ["T , F", "F , F", "F , T", "T , T"]
