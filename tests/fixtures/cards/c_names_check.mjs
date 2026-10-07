/* The distinguishing-name guard (Q-1780).
 *
 * ChatGPT pass, three strategies "Simple Mean Reversion (Top 20 Perps,
 * 20D)" / "…10D)" / "…30D)": the comparison's column headers all read
 * "Simple Mean Reversion (To…" and each run receipt read "Simple Mean
 * Reversion (Top 20 Perps…" — the part that differs was the part cut.
 * This renders those names in real Chromium and reads what is shown.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_names.py`` makes
 * the assertions.
 *
 * Usage: node c_names_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>]
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
const fixtureDir = arg("fixtures", here);
if (!cardsDir) {
  console.error("usage: c_names_check.mjs --cards <dir> [--fixtures <dir>]");
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

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
  const nm = document.querySelector(".rr-name");
  return {
    headName: nm ? txt(nm) : null,
    headTitle: nm ? nm.title : null,
    columns: q("table.cmp th.run").map((el) =>
      txt(el).replace(/\s*baseline$/, ""),
    ),
    legend: q(".legend > span").map(txt),
    leads: q(".row-item .lead").map((el) =>
      txt(el).replace(/\s*baseline$/, ""),
    ),
    // The narrow comparison's structure (Q-1784): the header row, how many
    // labels are repeated inside the blocks, and where each value column
    // starts in every block and in the header.
    blockHead: q(".cmp-blocks .row-head .k").map(txt),
    blockLabels: q(".cmp-blocks .row-item .k").length,
    headX: q(".cmp-blocks .row-head .k").map((el) =>
      Math.round(el.getBoundingClientRect().left),
    ),
    colX: q(".cmp-blocks .row-item").map((item) =>
      Array.from(item.querySelectorAll(".field")).map((el) =>
        Math.round(el.getBoundingClientRect().left),
      ),
    ),
    // The receipt's name as SHOWN, and whether the host clipped it.
    rrName: (() => {
      const n = document.querySelector(".rr .rr-name");
      return n
        ? {
            shown: n.textContent,
            full: n.getAttribute("data-full") || n.textContent,
            clipped: n.scrollWidth > n.clientWidth + 1,
          }
        : null;
    })(),
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(kind, envelope, width) {
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 1200 },
  });
  await page.setContent(harness(cardHtml(kind), envelope));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForTimeout(600);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const m = await frame.evaluate(PROBE);
  await page.close();
  return m;
}

const NAMES = [
  "Simple Mean Reversion (Top 20 Perps, 20D)",
  "Simple Mean Reversion (Top 20 Perps, 10D)",
  "Simple Mean Reversion (Top 20 Perps, 30D)",
];
function cross3() {
  const env = readFixture("compare_cross.envelope.json");
  const runs = env.view.runs;
  while (runs.length < 3) runs.push(JSON.parse(JSON.stringify(runs[1])));
  runs.forEach((r, i) => {
    r.label = NAMES[i];
  });
  env.view.name = "3 strategies";
  return env;
}
function renamedReceipt(file, name) {
  const env = readFixture(file);
  env.view.name = name;
  if (env.strategy_name) env.strategy_name = name;
  return env;
}

const runs = {
  cross3Wide: measure("compare", cross3(), 768),
  cross3Narrow: measure("compare", cross3(), 400),
  // CONTROL: one strategy's versions share no long stem.
  compare4: measure("compare", readFixture("compare_4.envelope.json"), 768),
  runReceipt481: measure(
    "backtest",
    renamedReceipt("c_bt_receipt_completed.envelope.json", NAMES[1]),
    481,
  ),
  saveReceipt360: measure(
    "strategy",
    renamedReceipt("strategy_receipt.envelope.json", NAMES[2]),
    360,
  ),
  // CONTROL: a name that fits is shown whole.
  runReceipt720: measure(
    "backtest",
    readFixture("c_bt_receipt_completed.envelope.json"),
    720,
  ),
};
const out = { names: NAMES };
for (const [k, v] of Object.entries(runs)) out[k] = await v;
out.compare4Labels = readFixture("compare_4.envelope.json").view.runs.map(
  (r) => r.label,
);
console.log(JSON.stringify(out, null, 1));
await browser.close();
