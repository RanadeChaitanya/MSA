"""Exact JSON the DeepSeek chat pass must return for ONE OCR page (fix formatting, flag doubts, never invent).
  python claude_pipeline/deepseek_schema.py        # validates the built-in example and writes deepseek_schema.json
DeepSeek JSON mode does not enforce a schema, so every reply is validated with PageResult and retried or flagged on failure.
"""
import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

Subject = Literal["Physics", "Chemistry", "Biology"]
OptKey = Literal["A", "B", "C", "D"]

FlagCode = Literal[
    "STEM_TRUNCATED", "STEM_DUPLICATED", "OPTION_COUNT", "OPTION_DUPLICATE", "OPTION_EMPTY", "OPTION_ORDER",
    "FIGURE_REFERENCED_NO_BOX", "FIGURE_BOX_NO_REFERENCE", "TABLE_UNPARSED", "NUMBERING_GAP", "NOISE_REMAINS",
    "SYMBOL_MISSING_SUSPECTED",   # a subscript, superscript, hat, bar or Greek letter may have been dropped
    "SUBSCRIPT_SUSPECTED", "GREEK_OR_PRIME_SUSPECTED", "OCR_CHAR_CONFUSION",  # I/1/l, O/0, gamma/prime ...
    "UNIT_SUSPECT", "NUMBER_SUSPECT", "CHEM_NAME_FORMULA_MISMATCH", "ANSWER_INCONSISTENT", "OTHER",
    "SOURCE_DEFECT",      # the book itself is wrong or incomplete (not fixed, only reported)
    "REPAIR_UNCERTAIN",   # a repair was made but the evidence is thin
]
EditType = Literal["format", "remove_noise", "split_options", "merge_blocks", "dedupe", "restore_symbol", "table_to_text",
                   "repair_symbol", "repair_expression"]


class Evidence(BaseModel):
    kind: Literal["internal", "domain", "solvability"]
    quote: str = Field(description="internal: the exact text of THIS question that supports the repair; domain: the standard law or definition; solvability: what becomes solvable")


class Edit(BaseModel):
    """One change made to the OCR text. The code guard checks every edit; restore_symbol edits are always re-checked by Sonnet."""
    field: str = Field(description="'stem' or 'options.A'..'options.D'")
    type: EditType
    before: str = Field(description="exact OCR text that was replaced")
    after: str
    basis: str = Field(description="text-only reason, e.g. 'options use D_1 and D_2'. Required for restore_symbol and repair_*")
    evidence: list[Evidence] = Field(default_factory=list, description="required (non-empty) for repair_symbol / repair_expression")
    risk: Literal["low", "medium", "high"] = "low"


class Flag(BaseModel):
    code: FlagCode
    field: str = Field(description="'stem', 'options.A'.., 'figure', or 'question'")
    severity: Literal["low", "medium", "high"]
    detail: str = Field(description="one sentence, what looks wrong and why")
    needs_image: bool = Field(description="true if only looking at the page can settle it")


class Question(BaseModel):
    subject: Optional[Subject] = Field(description="null when it cannot be told from the text")
    number: int
    source_blocks: list[int] = Field(description="indexes of the OCR blocks this question was built from")
    stem: str = Field(description="cleaned question text; maths as $...$ LaTeX, no \\mathrm clutter; keep [figure] markers")
    options: dict[OptKey, str] = Field(description="all four keys; empty string when an option is only a picture")
    figure_ids: dict[Literal["question", "A", "B", "C", "D"], list[str]] = Field(
        description="OCR figure box IDs passed through untouched (e.g. 'p3_img1'); never invented")
    status: Literal["clean", "fixed", "repaired", "flag"] = Field(
        description="clean = no change, fixed = formatting edits only, repaired = repair edits with evidence and solvable = yes, flag = unresolved doubt")
    solvable: Literal["yes", "ambiguous", "no"] = Field(default="yes", description="can a student solve the question from the text as you return it")
    solvable_reason: str = ""
    solved_option: Optional[OptKey] = Field(default=None, description="the option your own quick solution supports; compared with the answer key later; never used to choose a repair")
    solved_confidence: float = Field(default=0.0, ge=0, le=1)
    edits: list[Edit]
    flags: list[Flag]
    confidence: float = Field(ge=0, le=1)


class DroppedBlock(BaseModel):
    block_index: int
    reason: Literal["header_noise", "footer_noise", "duplicate", "not_a_question"]
    text: str


class PageResult(BaseModel):
    page: int
    questions: list[Question]
    dropped_blocks: list[DroppedBlock]
    page_flags: list[Flag]


EXAMPLE = PageResult(
    page=3,
    questions=[
        Question(
            subject="Physics", number=28, source_blocks=[14, 15],
            stem="In case of well of death which is a vertical cylindrical wall of radius 'r' inside which vehicle is driven in horizontal circles. "
                 "If 'm' is mass of vehicle, 'V' is the velocity and '$\\mu_s$' is the coefficient of static friction between the wheels of vehicle "
                 "and walls then correct relation is [g = acceleration due to gravity]",
            options={"A": "$V^2 \\le \\frac{rg}{\\mu_s}$", "B": "$V \\le \\frac{rg}{\\mu_s}$", "C": "$V^2 \\ge \\frac{rg}{\\mu_s}$", "D": "$V \\ge \\frac{rg}{\\mu_s}$"},
            figure_ids={"question": [], "A": [], "B": [], "C": [], "D": []},
            status="flag",
            edits=[Edit(field="stem", type="restore_symbol", before="'\\mu'", after="'$\\mu_s$'",
                        basis="stem says 'coefficient of static friction', which is written mu_s"),
                   Edit(field="options.A", type="restore_symbol", before="\\mu", after="\\mu_s", basis="same symbol as the stem")],
            flags=[Flag(code="SUBSCRIPT_SUSPECTED", field="options.A", severity="medium",
                        detail="OCR wrote mu without the subscript; restored from the stem's 'static friction' wording", needs_image=True)],
            confidence=0.8),
        Question(
            subject="Physics", number=26, source_blocks=[20, 21],
            stem="In thermodynamic process, which of the following statements is not true?",
            options={"A": "In an adiabatic process, the system is insulated from the surroundings.",
                     "B": "In an isochoric process, the pressure remains constant.",
                     "C": "In an isothermal process, the temperature remains constant.",
                     "D": "In an adiabatic process, $pV^{\\gamma}$ = constant."},
            figure_ids={"question": [], "A": [], "B": [], "C": [], "D": []},
            status="flag",
            edits=[Edit(field="options.D", type="restore_symbol", before="$\\mathrm{pv}^{\\prime}$", after="$pV^{\\gamma}$",
                        basis="adiabatic law is pV^gamma = constant")],
            flags=[Flag(code="GREEK_OR_PRIME_SUSPECTED", field="options.D", severity="medium",
                        detail="OCR gave a prime mark where the adiabatic law has gamma", needs_image=True)],
            confidence=0.85),
    ],
    dropped_blocks=[],
    page_flags=[],
)

if __name__ == "__main__":
    PageResult.model_validate(EXAMPLE.model_dump())  # round-trip check
    Path(__file__).with_name("deepseek_schema.json").write_text(json.dumps(PageResult.model_json_schema(), indent=1), encoding="utf-8")
    print("schema ok;", len(json.dumps(PageResult.model_json_schema())), "chars; example questions:", len(EXAMPLE.questions))
