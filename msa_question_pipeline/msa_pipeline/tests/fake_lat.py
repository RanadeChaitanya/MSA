"""Offline stand-in for Landing AI (same interface as pipeline.lat.client.LatClient).
Builds a synthetic one-page worksheet (2 columns, tables-as-text-blocks, figures, a missed figure that only
repass can find, a 'Correct Option' line, a watermark and digit-noise) and answers parse/extract exactly in the
documented Gen2 shapes (structure tree, markdown ranges, extraction_metadata spans)."""
from __future__ import annotations

W, H = 1200, 1700


class FakeLat:
    def __init__(self):
        self.credits = 0.0
        self.calls = {}
        self.cache_hits = 0
        self.repass_calls = 0
        self.blocks = []        # dicts: id,type,text,box(px)
        self.questions = []     # dicts: number, stem:[ids], opts:{L:[ids]}, correct
        self._n = 0
        self._build()

    # ---------- synthetic page ----------
    def add(self, typ, text, box):
        bid = f"{typ}-{self._n}"
        self._n += 1
        self.blocks.append({"id": bid, "type": typ, "text": text, "box": box})
        return bid

    def question(self, number, x0, y0, stem_text, opts, fig=None, answer=None, stem_h=70, opt_dy=80):
        gx = x0 - 50
        self.add("text", str(number), (gx, y0, gx + 28, y0 + 24))
        stem = [self.add("text", stem_text, (x0, y0, x0 + 450, y0 + stem_h))]
        yy = y0 + stem_h + 20
        if fig:
            fx0, fy0, fx1, fy1 = fig
            stem.append(self.add("figure", "diagram", (x0 + fx0, yy + fy0, x0 + fx1, yy + fy1)))
            yy += fy1 + 20
        o_ids = {}
        for i, L in enumerate("ABCD"):
            col, row = i % 2, i // 2
            ox, oy = x0 + col * 230, yy + row * opt_dy
            ids = [self.add("text", L, (ox, oy, ox + 14, oy + 16))]
            kind, val = opts[L]
            if kind == "text":
                ids.append(self.add("text", val, (ox + 25, oy + 20, ox + 120, oy + 46)))
            elif kind == "fig":
                ids.append(self.add("figure", "opt", (ox + 20, oy + 20, ox + 130, oy + 66)))
            o_ids[L] = ids
        correct = None
        if answer:
            ay = yy + 2 * opt_dy + 5
            self.add("text", "Correct Option:", (x0, ay, x0 + 120, ay + 18))
            self.add("text", answer[1], (x0, ay + 22, x0 + 90, ay + 50))
            correct = answer[0]
        self.questions.append({"number": number, "stem": stem, "opts": o_ids, "correct": correct,
                               "stem_text": stem_text, "opt_text": {L: (v[1] if v[0] == "text" else "") for L, v in opts.items()}})
        return yy + 2 * opt_dy

    def _build(self):
        self.add("text", "L.No. 1 Rotational Dynamics", (300, 20, 900, 70))                 # banner (ignored)
        T = lambda s: ("text", s)
        self.question(1, 110, 100, "A square lamina of side b has same mass as a disc. The ratio is",
                      {"A": T("1:1"), "B": T("$\\sqrt{3}:1$"), "C": T("$\\sqrt{6}:1$"), "D": T("1:$\\sqrt{3}$")})
        self.question(2, 110, 420, "A solid sphere rolls without slipping. The ratio of energies is",
                      {"A": T("7/2"), "B": T("5/2"), "C": T("7/3"), "D": T("5/4")}, fig=(60, 0, 300, 120))
        # Q3: B = parsed figure ; C = image-only, figure NOT detected by parse (only ink) ; digit noise 2,3
        self.question(3, 110, 900, "Two discs of same mass are made from different materials. The ratio is",
                      {"A": T("1:2"), "B": ("fig", ""), "C": ("fig_missed", ""), "D": T("2:3")})
        # remove the parse figure for option C (simulate a miss) and keep ink: handled in draw + here
        qd = self.questions[-1]
        for bid in list(qd["opts"]["C"]):
            blk = next(b for b in self.blocks if b["id"] == bid)
            if blk["type"] == "figure":
                self.blocks.remove(blk)
                qd["opts"]["C"].remove(bid)
        self.question(4, 700, 100, "A particle of mass m rotates in a circle. The centripetal force is",
                      {"A": T("$\\frac{L^2}{mr}$"), "B": T("$\\frac{L^2}{mr^2}$"), "C": T("mL/r"), "D": T("L/r")},
                      answer=("A", "$\\frac{L^2}{mr}$"))
        self.add("text", "Matrix Science Academy", (200, 600, 1100, 900))                  # watermark
        # digit noise that must NOT become question numbers (fraction-piece style)
        self.add("text", "2", (130, 1010, 146, 1030))
        self.add("text", "3", (130, 1050, 146, 1070))

    # ---------- LatClient interface ----------
    def _markdown_and_ranges(self):
        md, pos, rng = "", 0, {}
        for b in self.blocks:
            text = f'<figure type="DIAGRAM">{b["text"]}</figure>' if b["type"] == "figure" else b["text"]
            rng[b["id"]] = (pos, pos + len(text))
            md += text + "\n\n"
            pos = len(md)
        md += "<!-- doc_id=fake-doc -->"
        return md, rng

    @staticmethod
    def _nbox(box, w=W, h=H):
        x0, y0, x1, y1 = box
        return {"xmin": x0 / w, "ymin": y0 / h, "xmax": x1 / w, "ymax": y1 / h}

    def parse(self, data, filename, model=None, options=None):
        self.calls["parse"] = self.calls.get("parse", 0) + 1
        if filename.endswith(".png"):                    # region repass: one figure filling the crop
            self.repass_calls += 1
            md = '<figure type="DIAGRAM">x</figure>'
            el = {"id": "figure-0", "type": "figure", "markdown": md,
                  "grounding": {"page": 1, "range": {"start": 0, "end": len(md)},
                                "box": {"xmin": 0.1, "ymin": 0.1, "xmax": 0.9, "ymax": 0.9}}}
            return {"markdown": md, "structure": {"type": "document", "children": [
                {"type": "page", "status": "ok", "grounding": {"page": 1}, "children": [el]}]},
                    "metadata": {"job_id": "x", "failed_pages": []}}
        md, rng = self._markdown_and_ranges()
        els = []
        for b in self.blocks:
            s, e = rng[b["id"]]
            els.append({"id": b["id"], "type": b["type"], "markdown": md[s:e],
                        "grounding": {"page": 1, "range": {"start": s, "end": e}, "box": self._nbox(b["box"])}})
        return {"markdown": md, "structure": {"type": "document", "children": [
            {"type": "page", "status": "ok", "grounding": {"page": 1}, "children": els}]},
                "metadata": {"job_id": "fake-doc", "failed_pages": []}}

    def extract(self, markdown, schema):
        _, rng = self._markdown_and_ranges()
        items, metas = [], []

        def leaf(value, ids):
            return {"value": value, "spans": [[rng[i][0], rng[i][1]] for i in ids if i in rng]}
        for q in self.questions:
            it = {"number": str(q["number"]), "stem": q["stem_text"], "correct_option": q["correct"] or ""}
            mt = {"number": leaf(str(q["number"]), []), "stem": leaf(q["stem_text"], q["stem"]),
                  "correct_option": leaf(it["correct_option"], [])}
            for L in "ABCD":
                txt = q["opt_text"].get(L, "")
                it[f"option_{L.lower()}"] = txt
                mt[f"option_{L.lower()}"] = leaf(txt, q["opts"][L][1:] if txt else [])
            items.append(it)
            metas.append(mt)
        return {"extraction": {"questions": items}, "extraction_metadata": {"questions": metas},
                "metadata": {"credit_usage": 1.0}}

    def ground(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError
