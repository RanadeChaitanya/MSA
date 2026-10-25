// Gold-set review page: the 50 most formula-heavy questions, scan crop next to our KaTeX rendering.
// node gold_page.js <paper.json> <out.html>
const fs = require("fs"), path = require("path");
const katex = require("katex"); require("katex/contrib/mhchem");
const [, , src, out] = process.argv;
const g = JSON.parse(fs.readFileSync(src, "utf8"));
const crops = path.resolve(__dirname, "..", "ocr_ab", "crops_all");
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const tex = t => (t || "").split(/(\$[^$]+\$)/).map(p => p.startsWith("$") && p.length > 1
  ? katex.renderToString(p.slice(1, -1), { throwOnError: false }) : esc(p).replace(/\n/g, "<br>")).join("");
const all = q => [q.stem, ...Object.values(q.options || {})].filter(x => typeof x === "string").join(" ");
const score = q => (all(q).match(/\$[^$]+\$/g) || []).length + 2 * (all(q).match(/\\(frac|sqrt|ce|xrightarrow|hat|vec)/g) || []).length;
const pick = [...g.questions].sort((a, b) => score(b) - score(a)).slice(0, 50).sort((a, b) => a.qid < b.qid ? -1 : 1);
const img = f => fs.existsSync(f) ? `<img src="data:image/png;base64,${fs.readFileSync(f).toString("base64")}">` : "";
const cards = pick.map(q => {
  const id = q.qid.split("_P01_")[1];
  return `<section><header><b>${id}</b><label><input type="checkbox" data-q="${id}"> differs from the book</label>
<input class="note" data-n="${id}" placeholder="what is wrong (optional)"></header>
<div class="row"><div class="scan">${img(path.join(crops, q.qid + ".png"))}${img(path.join(crops, q.qid + "_cont.png"))}</div>
<div class="ours"><p>${tex(q.stem)}</p>${Object.entries(q.options || {}).map(([k, v]) => `<p>(${k}) ${typeof v === "string" ? tex(v) : ""}</p>`).join("")}</div></div></section>`;
}).join("");
const css = fs.readFileSync(path.resolve(__dirname, "node_modules/katex/dist/katex.min.css"), "utf8")
  .replace(/url\(fonts\//g, "url(file:///" + path.resolve(__dirname, "node_modules/katex/dist/fonts").replace(/\\/g, "/") + "/");
fs.writeFileSync(out, `<!doctype html><html><head><meta charset="utf-8"><title>Paper 1 LaTeX gold set</title><style>${css}
body{font-family:Georgia,serif;margin:16px;background:#fafafa;color:#111}section{background:#fff;border:1px solid #ddd;border-radius:8px;margin:14px 0;padding:10px}
header{display:flex;gap:16px;align-items:center;margin-bottom:8px}.note{flex:1;padding:4px}.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.scan img{max-width:100%;display:block;margin-bottom:6px}.ours{font-size:19px}p{margin:4px 0}
#bar{position:sticky;top:0;background:#111;color:#fff;padding:10px;border-radius:6px;display:flex;gap:12px;align-items:center;z-index:2}
button{padding:6px 12px}@media(max-width:800px){.row{grid-template-columns:1fr}}</style></head><body>
<div id="bar"><b>Left: book scan. Right: our JSON rendered.</b> Tick any question where the right side differs. <button id="copy">Copy my list</button><span id="msg"></span></div>
${cards}<script>
document.getElementById("copy").onclick=()=>{const L=[...document.querySelectorAll("input[type=checkbox]:checked")].map(c=>{const n=document.querySelector('[data-n="'+c.dataset.q+'"]').value;return c.dataset.q+(n?": "+n:"")});
const t=L.length?L.join("\\n"):"all 50 match";navigator.clipboard.writeText(t).then(()=>{document.getElementById("msg").textContent="Copied "+(L.length||0)+" item(s). Paste it in chat."},()=>{prompt("Copy this:",t)})};
</script></body></html>`);
console.log(`gold page: ${pick.length} questions -> ${out}`);
