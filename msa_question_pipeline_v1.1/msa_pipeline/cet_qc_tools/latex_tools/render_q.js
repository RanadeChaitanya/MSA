// One HTML per question (the 50 gold questions) for a rendered-vs-scan check. node render_q.js <paper.json> <outdir>
const fs = require("fs"), path = require("path");
const katex = require("katex"); require("katex/contrib/mhchem");
const [, , src, outdir] = process.argv; fs.mkdirSync(outdir, { recursive: true });
const g = JSON.parse(fs.readFileSync(src, "utf8"));
const css = path.resolve(__dirname, "node_modules/katex/dist/katex.min.css").replace(/\\/g, "/");
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const tex = t => (t || "").split(/(\$[^$]+\$)/).map(p => p.startsWith("$") && p.length > 1
  ? katex.renderToString(p.slice(1, -1), { throwOnError: false }) : esc(p).replace(/\n/g, "<br>")).join("");
const all = q => [q.stem, ...Object.values(q.options || {})].filter(x => typeof x === "string").join(" ");
const score = q => (all(q).match(/\$[^$]+\$/g) || []).length + 2 * (all(q).match(/\\(frac|sqrt|ce|xrightarrow|hat|vec)/g) || []).length;
const pick = process.argv[4] === "all" ? g.questions : [...g.questions].sort((a, b) => score(b) - score(a)).slice(0, 50);
for (const q of pick) {
  const opts = Object.entries(q.options || {}).map(([k, v]) => `<p>(${k}) ${typeof v === "string" ? tex(v) : ""}${(q.option_images || {})[k] ? " [picture]" : ""}</p>`).join("");
  fs.writeFileSync(path.join(outdir, q.qid + ".html"), `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="file:///${css}">
<style>body{font-family:"Times New Roman",serif;font-size:24px;width:900px;margin:16px;background:#fff}p{margin:5px 0}</style></head><body><p>${tex(q.stem)}</p>${opts}</body></html>`);
}
fs.writeFileSync(path.join(outdir, "gold_ids.json"), JSON.stringify(pick.map(q => q.qid)));
console.log(pick.length, "question pages");
