"""JSON schema sent to Landing AI Extract (POST /v2/extract).

Extract returns VALUES + character ranges into the parse markdown. Ground then maps
those ranges to structure blocks (ids, pages, boxes). The schema therefore asks for
verbatim text only; geometry is never asked from the model.
"""

OPTION_LABELS = ["A", "B", "C", "D"]


def _opt(label: str) -> dict:
    return {
        "type": "string",
        "description": (
            f"Verbatim text of option {label} (math as LaTeX). Empty string if option {label} "
            "contains only an image/diagram. Do not include the letter label itself."
        ),
    }


QUESTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "description": (
                "Every multiple-choice question in the document, in reading order "
                "(left column top-to-bottom, then right column). Ignore page banners, "
                "titles, watermarks such as 'Matrix Science Academy', and page numbers."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string",
                               "description": "Question number printed in the left gutter, digits only."},
                    "stem": {"type": "string",
                             "description": ("Full question text exactly as printed, excluding the options, "
                                             "the number, and any 'Correct Option' line. Math as LaTeX.")},
                    "option_a": _opt("A"),
                    "option_b": _opt("B"),
                    "option_c": _opt("C"),
                    "option_d": _opt("D"),
                    "correct_option": {"type": ["string", "null"],
                                       "description": ("Single letter A-D ONLY if an answer line such as 'Correct Option' is "
                                                       "printed for this question; otherwise null.")},
                },
                "required": ["number", "stem"],
            },
        }
    },
    "required": ["questions"],
}
