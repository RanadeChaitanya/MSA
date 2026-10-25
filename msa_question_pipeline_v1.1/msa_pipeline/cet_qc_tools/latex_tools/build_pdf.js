// v10 review PDF source: change log (old vs new, both KaTeX-rendered) + every question (scan crop | our rendering).
// node build_pdf.js <old.json> <new.json> <out.html> [flags.json]  -> then Edge --print-to-pdf
const fs = require("fs"), path = require("path");
const katex = require("katex"); require("katex/contrib/mhchem");
const [, , oldF, newF, out, flagsF] = process.argv;
const A = Object.fromEntries(JSON.parse(fs.readFileSync(oldF, "utf8")).questions.map(q => [q.qid, q]));
const B = JSON.parse(fs.readFileSync(newF, "utf8")).questions;
const extra = flagsF && fs.existsSync(flagsF) ? JSON.parse(fs.readFileSync(flagsF, "utf8")) : {};
const crops = path.resolve(__dirname, "..", "ocr_ab", "crops_all");
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const tex = t => (t || "").split(/(\$[^$]+\$)/).map(p => p.startsWith("$") && p.length > 1
  ? katex.renderToString(p.slice(1, -1), { throwOnError: false }) : esc(p).replace(/\n/g, "<br>")).join("");
const img = f => fs.existsSync(f) ? `<img src="data:image/png;base64,${fs.readFileSync(f).toString("base64")}">` : "";
const short = q => q.qid.split("_P01_")[1];
const fields = q => [["stem", q.stem], ...["A", "B", "C", "D"].map(k => ["option " + k, (q.options || {})[k]])];
// change log
let rows = "", nChanged = 0;
for (const q of B) {
  const o = A[q.qid]; if (!o) continue;
  const ofs = Object.fromEntries(fields(o));
  for (const [f, v] of fields(q)) {
    if ((ofs[f] || "") === (v || "")) continue;
    nChanged++;
    rows += `<tr><td>${short(q)}</td><td>${f}</td><td>${tex(ofs[f] || "")}</td><td>${tex(v || "")}</td></tr>`;
  }
  if (o.correct_option !== q.correct_option) rows += `<tr><td>${short(q)}</td><td>answer key</td><td>${o.correct_option}</td><td>${q.correct_option}</td></tr>`;
}
const flagOf = q => [...(q.flags || []).map(f => typeof f === "string" ? f : f.code || ""), ...(extra[q.qid] || [])].filter(f => /^V9_|^V10_|SOLVER|EMPTY|MISMATCH/.test(f));
const cards = B.map(q => `<section><header><b>${short(q)}</b> &nbsp; key <b>${q.correct_option || "?"}</b>
${flagOf(q).length ? `<span class="flag">${[...new Set(flagOf(q))].join(", ")}</span>` : ""}${q.v9_note ? `<div class="note">${esc(q.v9_note)}</div>` : ""}</header>
<div class="row"><div class="scan">${img(path.join(crops, q.qid + ".png"))}${img(path.join(crops, q.qid + "_cont.png"))}</div>
<div class="ours"><p>${tex(q.stem)}</p>${Object.entries(q.options || {}).map(([k, v]) => `<p>(${k}) ${typeof v === "string" ? tex(v) : ""}${
  (q.option_images || {})[k] ? `<br>${img(path.resolve(__dirname, "..", q.option_images[k])).replace("<img ", '<img style="max-height:70px" ')}` : ""}</p>`).join("")}</div></div></section>`).join("");
const css = fs.readFileSync(path.resolve(__dirname, "node_modules/katex/dist/katex.min.css"), "utf8")
  .replace(/url\(fonts\//g, "url(file:///" + path.resolve(__dirname, "node_modules/katex/dist/fonts").replace(/\\/g, "/") + "/");
fs.writeFileSync(out, `<!doctype html><html><head><meta charset="utf-8"><title>Paper 1 v10</title><style>${css}
@page{size:A4;margin:12mm}body{font-family:Georgia,serif;color:#111;font-size:12px}h1{font-size:20px}h2{font-size:16px;margin-top:18px}
table{border-collapse:collapse;width:100%;font-size:11px}td,th{border:1px solid #ccc;padding:4px;vertical-align:top}th{background:#eee}
section{border:1px solid #ccc;border-radius:6px;margin:8px 0;padding:8px;page-break-inside:avoid}header{margin-bottom:6px}
.flag{background:#ffe08a;padding:1px 6px;border-radius:4px;margin-left:8px;font-size:11px}.note{font-size:11px;color:#7a4b00;margin-top:3px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.scan img{max-width:100%;display:block;margin-bottom:4px}.ours{font-size:14px}p{margin:3px 0}</style></head><body>
<h1>CET PCB 2024 — Paper 1 — v10</h1><p>Left: book scan. Right: our JSON rendered with KaTeX (the same renderer a website would use). ${B.length} questions.</p>
<h2>Changes from the previous version (${nChanged} fields)</h2><table><tr><th>Q</th><th>Field</th><th>Before</th><th>After</th></tr>${rows}</table>
<h2>All questions</h2>${cards}</body></html>`);
console.log(`html: ${B.length} questions, ${nChanged} changed fields -> ${out}`);
