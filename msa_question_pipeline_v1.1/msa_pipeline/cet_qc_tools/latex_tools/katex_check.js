// Strict KaTeX compile of every $...$ span in a paper JSON (mhchem loaded). Usage: node katex_check.js <paper.json> [out.json]
const fs = require("fs");
const katex = require("katex");
require("katex/contrib/mhchem");

const g = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const errors = [];
let n = 0;
for (const q of g.questions) {
  const fields = [["stem", q.stem], ...Object.entries(q.options || {}).map(([k, v]) => ["options." + k, v])];
  for (const [field, t] of fields) {
    if (typeof t !== "string" || !t) continue;
    if ((t.match(/\$/g) || []).length % 2) { errors.push({ qid: q.qid, field, latex: t.slice(0, 120), error: "odd number of $" }); continue; }
    for (const m of t.matchAll(/\$([^$]+)\$/g)) {
      n++;
      try {
        katex.renderToString(m[1], { strict: "error", throwOnError: true, displayMode: false });
      } catch (e) {
        errors.push({ qid: q.qid, field, latex: m[1], error: String(e.message).split("\n")[0].slice(0, 160) });
      }
    }
    // structure KaTeX accepts but the style guide forbids
    for (const m of t.matchAll(/\$([^$]+)\$/g)) {
      const s = m[1];
      if (/\\mathrm\{[^{}]*\\\\/.test(s)) errors.push({ qid: q.qid, field, latex: s, error: "line break \\\\ inside \\mathrm" });
      if (/\\mathrm\{[^{}]*\\(x?rightarrow|leftarrow|rightleftharpoons|xrightarrow)/.test(s)) errors.push({ qid: q.qid, field, latex: s, error: "arrow inside \\mathrm" });
      if (/\\mathrm\{[A-Za-z]{6,}\}/.test(s)) errors.push({ qid: q.qid, field, latex: s, error: "word in \\mathrm (use \\text)" });
    }
  }
}
if (process.argv[3]) fs.writeFileSync(process.argv[3], JSON.stringify(errors, null, 1));
console.log(`${n} formulas compiled strictly; ${errors.length} problems`);
for (const e of errors) console.log(" ", e.qid.split("_P01_")[1] || e.qid, e.field, "|", e.error, "|", e.latex.slice(0, 70));
