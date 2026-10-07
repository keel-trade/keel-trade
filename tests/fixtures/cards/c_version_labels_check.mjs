/* The version-label guard (Q-1792).
 *
 * claude.ai, staging dev-f960c13: three saved VERSIONS of one
 * mean-reversion strategy (lookback 20 / 10 / 30) compared as columns
 * headed "v1 BASELINE · v2 · v3" — nothing said which version was which.
 * The label is server-owned (`view.runs[i].label`); this renders the REAL
 * compare envelope the test builds and reads what the card SHOWS: the
 * column headers (and whether a header is clipped), the legend, and the
 * narrow form's run names.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_version_labels.py``
 * makes the assertions.
 *
 * Usage: node c_version_labels_check.mjs --cards <dir of {kind}.html>
 *          --envelopes <file: {arm: envelope}>
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../../../../..");
const require = createRequire(
  path.join(repoRoot, "services/keel-app/package.json"),
);

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const cardsDir = arg("cards");
const envFile = arg("envelopes");
if (!cardsDir || !envFile) {
  console.error(
    "usage: c_version_labels_check.mjs --cards <dir> --envelopes <file>",
  );
  process.exit(2);
}
const cardHtml = fs.readFileSync(path.join(cardsDir, "compare.html"), "utf8");
const envelopes = JSON.parse(fs.readFileSync(envFile, "utf8"));

function harness(card, envelope) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope).replace(/<\//g, "<\\/") +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:var(--w)}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
const f=document.getElementById("c");
function send(m){f.contentWindow.postMessage(m,"*");}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{
     theme:"dark",displayMode:"inline",availableDisplayModes:["inline"],styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",
     params:{content:[{type:"text",text:JSON.stringify(window.__ENV__)}]}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const PROBE = () => {
  const txt = (el) => ((el && el.innerText) || "").trim();
  const vis = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    return cs.display !== "none" && el.getBoundingClientRect().width > 0;
  };
  const q = (s) => Array.from(document.querySelectorAll(s)).filter(vis);
  // A header's own label text, without the "baseline" badge under it.
  const own = (el) =>
    Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent)
      .join("")
      .trim();
  return {
    columns: q("table.cmp th.run").map(own),
    clipped: q("table.cmp th.run").map(
      (el) => el.scrollWidth > el.clientWidth + 1,
    ),
    titles: q("table.cmp th.run").map((el) => el.title),
    legend: q(".legend > span:not(.rest)").map(txt),
    leads: q(".row-item .lead").map(own),
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(envelope, width) {
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 1400 },
  });
  await page.setContent(harness(cardHtml, envelope));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForTimeout(600);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const m = await frame.evaluate(PROBE);
  await page.close();
  return m;
}

const out = {};
for (const [arm, env] of Object.entries(envelopes)) {
  out[arm] = { wide: await measure(env, 768), narrow: await measure(env, 400) };
}
console.log(JSON.stringify(out, null, 1));
await browser.close();
