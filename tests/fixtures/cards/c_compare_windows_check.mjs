/* The compare-windows guard (Q-1802).
 *
 * claude.ai: two runs submitted either side of 00:00 UTC compared with
 * "Periods differ (A: 2024-07-27 00:00:00+00:00..2026-09-23 00:00:00+00:00,
 * B: …) — absolute metrics are not directly comparable." A one-day tail
 * is now a NOTE; a material difference is a warning, a header chip, and a
 * shaded band on the overlaid chart outside the window every run covers.
 * This renders the REAL compare envelopes the test builds and reads what
 * the card SHOWS: the note lines, the header chips, the receipt, and the
 * shaded rectangles (count, and their share of the plot width).
 *
 * Prints ONE JSON object on stdout; ``tests/test_compare_windows.py`` makes
 * the assertions.
 *
 * Usage: node c_compare_windows_check.mjs --cards <dir of {kind}.html>
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
    "usage: c_compare_windows_check.mjs --cards <dir> --envelopes <file>",
  );
  process.exit(2);
}
const cardHtml = fs.readFileSync(path.join(cardsDir, "compare.html"), "utf8");
const envelopes = JSON.parse(fs.readFileSync(envFile, "utf8"));

function harness(card, envelope, mode) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope).replace(/<\//g, "<\\/") +
    ";window.__MODE__=" +
    JSON.stringify(mode) +
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
     theme:"dark",displayMode:window.__MODE__,availableDisplayModes:["inline","fullscreen"],styles:{variables:{}}}}});}
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
  const svg = document.querySelector(".chart svg");
  const plot = svg ? svg.getBoundingClientRect().width : 0;
  const rects = Array.from(document.querySelectorAll(".chart rect.outside"));
  return {
    chart: !!svg,
    curves: document.querySelectorAll(".chart path.ml").length,
    shaded: rects.length,
    shadedShare: plot
      ? rects.reduce((a, r) => a + r.getBoundingClientRect().width, 0) / plot
      : 0,
    chips: Array.from(document.querySelectorAll(".cmp-head .chip")).map(txt),
    body: txt(document.getElementById("card-body")),
    ariaLabel: svg ? svg.getAttribute("aria-label") : null,
    // The value axis: the end-anchored labels left of the plot.
    yLabels: Array.from(
      document.querySelectorAll('.chart .axis text[text-anchor="end"]'),
    )
      .filter((t) => Number(t.getAttribute("x")) < 60)
      .map((t) => t.textContent),
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(envelope, width, mode) {
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 1600 },
  });
  await page.setContent(harness(cardHtml, envelope, mode));
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
  out[arm] = {
    inline: await measure(env, 720, "inline"),
    full: await measure(env, 960, "fullscreen"),
  };
}
console.log(JSON.stringify(out, null, 1));
await browser.close();
