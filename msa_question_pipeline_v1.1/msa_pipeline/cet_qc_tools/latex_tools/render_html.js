// Step 3a: render questions with KaTeX into HTML pages (8 questions per page) for screenshot + OCR.
// node render_html.js <paper.json> <outdir>
const fs = require("fs"), path = require("path");
const katex = require("katex");
require("katex/contrib/mhchem");
const [, , src, outdir] = process.argv;
fs.mkdirSync(outdir, { recursive: true });
const g = JSON.parse(fs.readFileSync(src, "utf8"));
const css = path.resolve(__dirname, "node_modules/katex/dist/katex.min.css").replace(/\\/g, "/");
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const tex = t => (t || "").split(/(\$[^$]+\$)/).map(p => p.startsWith("$") && p.length > 1
  ? katex.renderToString(p.slice(1, -1), { throwOnError: false }) : esc(p).replace(/\n/g, "<br>")).join("");
const short = q => q.qid.split("_P01_")[1] || q.qid;
const per = 8, map = [];
for (let i = 0; i < g.questions.length; i += per) {
  const qs = g.questions.slice(i, i + per), n = String(i / per + 1).padStart(2, "0");
  const body = qs.map(q => `<div class="q"><p><b>QID ${short(q)}</b></p><p>${tex(q.stem)}</p>` +
    Object.entries(q.options || {}).map(([k, v]) => `<p>(${k}) ${typeof v === "string" ? tex(v) : ""}</p>`).join("") + "</div>").join("");
  fs.writeFileSync(path.join(outdir, `render_${n}.html`), `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="file:///${css}"><style>body{font-family:"Times New Roman",serif;font-size:22px;width:960px;margin:20px;background:#fff;color:#000}
.q{margin-bottom:26px}p{margin:4px 0}</style></head><body>${body}</body></html>`);
  map.push({ page: n, qids: qs.map(q => q.qid) });
}
fs.writeFileSync(path.join(outdir, "map.json"), JSON.stringify(map, null, 1));
console.log(`${map.length} html pages`);
