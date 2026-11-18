/* A screen drawn through the strategy card (Q-3639).
 *
 * compose, get and fork draw a screen with the strategy card. The founder's
 * claude.ai test read raw slot names (`funding_now`) where the screen's view
 * names its columns, and a link that said "Open strategy in Keel" on a
 * screen. This renders the REAL envelopes the test builds, under each host
 * adapter (the full profile's and the listed connector's), and reads what
 * the card SHOWS: the outputs rows (their text, unit chip and tooltip) and
 * the adapter's link-row labels.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_screen_strategy_card.py``
 * makes the assertions.
 *
 * Usage: node c_screen_strategy_card_check.mjs --cards <dir of {name}.html>
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
    "usage: c_screen_strategy_card_check.mjs --cards <dir> --envelopes <file>",
  );
  process.exit(2);
}
const cards = {};
for (const name of fs.readdirSync(cardsDir)) {
  if (name.endsWith(".html"))
    cards[name.slice(0, -5)] = fs.readFileSync(
      path.join(cardsDir, name),
      "utf8",
    );
}
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
     theme:"dark",displayMode:"inline",availableDisplayModes:["inline","fullscreen"],
     styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",
     params:{content:[{type:"text",text:JSON.stringify(window.__ENV__)}],
       structuredContent:window.__ENV__}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const PROBE = () => {
  const txt = (el) => ((el && el.innerText) || "").trim();
  const q = (s) => Array.from(document.querySelectorAll(s));
  return {
    outputs: q(".sv-out .sv-oname").map(txt),
    tooltips: q(".sv-out .sv-oname").map((el) => el.title),
    units: q(".sv-out").map((row) => txt(row.querySelector(".sv-ounit"))),
    links: q("#link-row .button-link").map(txt),
    blocks: q("#card-body *").length,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(card, envelope) {
  const page = await browser.newPage({
    viewport: { width: 768 + 48, height: 1400 },
  });
  await page.setContent(harness(card, envelope));
  await page.addStyleTag({ content: ".frame{--w:768px;width:768px}" });
  await page.waitForTimeout(600);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const m = await frame.evaluate(PROBE);
  await page.close();
  return m;
}

const out = {};
for (const [card, html] of Object.entries(cards)) {
  out[card] = {};
  for (const [arm, env] of Object.entries(envelopes)) {
    out[card][arm] = await measure(html, env);
  }
}
console.log(JSON.stringify(out, null, 1));
await browser.close();
