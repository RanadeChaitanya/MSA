"""Strict reconstruction of questions from pipeline data only (stem, options, figure crops, key). No model involved.
tex(s): crude LaTeX -> HTML (sub/sup, fractions, Greek). render_question(out_doc, src_doc, q): one A4 page per question.
"""
import re
import fitz
from cet_figures import grow

GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ",
         "omega": "ω", "Omega": "Ω", "phi": "φ", "epsilon": "ε", "rho": "ρ", "tau": "τ", "times": "×", "circ": "°", "cdot": "·",
         "pm": "±", "leq": "≤", "geq": "≥", "le": "≤", "ge": "≥", "propto": "∝", "infty": "∞", "rightarrow": "→", "to": "→", "hat": "", "Delta": "Δ",
         "eta": "η", "nu": "ν", "psi": "ψ", "Phi": "Φ", "Sigma": "Σ", "approx": "≈", "neq": "≠", "degree": "°", "prime": "′"}
CSS = "body{font-family:sans-serif;font-size:11pt} .q{font-weight:bold} .o{margin-left:14pt} sub,sup{font-size:8pt} .k{color:#060} .f{color:#a00}"


def tex(s):
    for _ in range(4):
        s = re.sub(r"\\(?:mathrm|mathbf|text|bf|rm|mathit|boldsymbol|vec|overline|bar)\{([^{}]*)\}", r"\1", s)
        s = re.sub(r"\\hat\{([^{}]*)\}", r"\1&#770;", s)
        s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
        s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
        s = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
        s = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"\^(\\?\w)", r"<sup>\1</sup>", s)
    s = re.sub(r"_(\\?\w)", r"<sub>\1</sub>", s)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK.get(m[1], ""), s)
    s = re.sub(r"\\\(|\\\)|\\\[|\\\]|\\left|\\right|\\[,;! ]|\$", "", s)
    s = s.replace("{", "").replace("}", "")
    return s.replace("&", "&amp;") if "&amp;" not in s and "&#" not in s else s


def _fig(src, f, max_w, max_h):
    page = src[f["page"] - 1]; r = page.rect; b = f["box"]
    clip = grow(page, fitz.Rect(b[0] / 1000 * r.width, b[1] / 1000 * r.height, b[2] / 1000 * r.width, b[3] / 1000 * r.height))
    w = min(max_w, clip.width * 1.8); h = w * clip.height / clip.width
    if h > max_h:
        w, h = w * max_h / h, max_h
    return page.get_pixmap(dpi=200, clip=clip), w, h


def render_question(out, src, q):
    pg = out.new_page(width=595, height=842); y = 30
    flags = ", ".join(r["code"] for r in q["reasons"])
    head = (f"<p class='q'>{q['qid']} <small>(source page {q['page']}, key <span class='k'>{q['correct_option']}</span>, {q['verdict']}"
            f"{', human verified' if q.get('human_verified') else ''})</small></p>" + (f"<p class='f'><small>{flags}</small></p>" if flags else ""))
    def put(html, h):
        nonlocal y
        spare, _ = pg.insert_htmlbox(fitz.Rect(40, y, 555, min(y + h, 830)), html, css=CSS)
        y += max(h - max(spare, 0), 14) + 4
    put(head, 50)
    put(f"<p>{tex(q['stem'])}</p>", 170)
    for f in q["figures"]["question"]:
        pix, w, h = _fig(src, f, 440, 190)
        pg.insert_image(fitz.Rect(40, y, 40 + w, y + h), pixmap=pix); y += h + 8
    for L in "ABCD":
        t = tex(q["options"].get(L, ""))
        put(f"<p class='o'>({L}) {t}</p>", 34)
        for f in q["figures"][L]:
            pix, w, h = _fig(src, f, 300, 110)
            pg.insert_image(fitz.Rect(60, y, 60 + w, y + h), pixmap=pix); y += h + 6
    return pg
