/* The paper states of the live card (Q-7603).
 *
 * Drives the served live card (the paper fragment + card-live.js) in real
 * Chromium through BOTH dialects with results the REAL SDK produced over the
 * FastMCP wire (`--cases <file>`, written by ``tests/test_paper_card.py``):
 * a paper start, a pause, a stop, the slot-cap and timeframe-floor walls, the
 * monitor's overview of a paper run before its first fill and with history,
 * and a real run's overview (the control).
 *
 * Each card file in `--cards` (the full profile's `live.html`, the listed
 * connector's `live_listed.html`) draws the cases filed under its own name:
 * `{"live": {case: {result, input}}, "live_listed": {...}}`.
 *
 * Prints ONE JSON object on stdout: {"<card>/<dialect>/<case>": probe}.
 *
 * Usage: node c_paper_card_check.mjs --cards <dir> --cases <file>
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

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : null;
}
const cardsDir = arg("cards");
const casesFile = arg("cases");
if (!cardsDir || !casesFile) {
  console.error("usage: c_paper_card_check.mjs --cards <dir> --cases <file>");
  process.exit(2);
}
const cards = fs
  .readdirSync(cardsDir)
  .filter((f) => f.endsWith(".html"))
  .map((f) => [
    f.replace(/\.html$/, ""),
    fs.readFileSync(path.join(cardsDir, f), "utf8"),
  ]);
const cases = JSON.parse(fs.readFileSync(casesFile, "utf8"));

const esc = (v) => JSON.stringify(v).replace(/<\//g, "<\\/");

/* MCP Apps host: one `tool-result` carrying the whole CallToolResult. */
function mcpHarness(card, result) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:720px}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>window.__CARD__=${esc(card)};window.__RESULT__=${esc(result)};
const f=document.getElementById("c");
function send(m){f.contentWindow.postMessage(m,"*");}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{
     theme:"light",displayMode:"inline",availableDisplayModes:["inline"],styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:window.__RESULT__});}
 else if(m.method==="ui/notifications/size-changed"){f.style.height=m.params.height+"px";}
 else if(m.id!=null){send({jsonrpc:"2.0",id:m.id,result:{}});}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

/* ChatGPT: the globals exist before the adapter runs; `toolOutput` is the
 * result's structuredContent and `toolResponseMetadata` its `_meta`. */
function openaiCard(card, input, result) {
  const shim =
    "<script>window.openai={theme:'light',displayMode:'inline',maxHeight:900," +
    "callTool:function(){return Promise.resolve({})},openExternal:function(){}," +
    "sendFollowUpMessage:function(){}," +
    "requestDisplayMode:function(){return Promise.resolve({mode:'inline'})}," +
    "notifyIntrinsicHeight:function(){}," +
    `toolInput:${esc(input)},toolOutput:${esc(result.structuredContent ?? null)},` +
    `toolResponseMetadata:${esc(result._meta ?? {})}};</script>`;
  return card.replace(/<body([^>]*)>/, (_m, attrs) => `<body${attrs}>${shim}`);
}

const PROBE = () => {
  const vis = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const head = document.getElementById("card-head");
  const meta = document.getElementById("head-meta");
  const body = document.getElementById("card-body");
  const row = document.getElementById("link-row");
  const link = row && row.querySelector("a");
  return {
    text: (document.getElementById("root").innerText || "").trim(),
    headVisible: vis(head),
    name: (meta && meta.querySelector(".head-name")?.textContent) || null,
    chips: meta
      ? Array.from(meta.querySelectorAll(".chip")).map((c) => c.textContent)
      : [],
    tiles: document.querySelectorAll(".stat").length,
    charts: body ? body.querySelectorAll("svg path").length : 0,
    kv: Array.from(document.querySelectorAll("table.kv tr")).map((tr) =>
      Array.from(tr.querySelectorAll("td")).map((td) => td.textContent),
    ),
    notes: Array.from(document.querySelectorAll("#card-body .note")).map(
      (n) => n.textContent,
    ),
    planLimit: vis(document.querySelector(".plan-limit")),
    headline: document.querySelector(".pl-headline")?.textContent || null,
    errLine: vis(document.querySelector(".err-line")),
    linkVisible: vis(row),
    linkLabel: link ? link.textContent : null,
    planLinks: document.querySelectorAll(".plan-limit a").length,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function run(html) {
  const page = await browser.newPage({
    viewport: { width: 768, height: 1000 },
  });
  await page.setContent(html);
  await page.waitForTimeout(700);
  const frame =
    page.frames().find((fr) => fr !== page.mainFrame()) || page.mainFrame();
  const m = await frame.evaluate(PROBE);
  await page.close();
  return m;
}

const jobs = [];
for (const [cardName, card] of cards) {
  // Each card draws the results of its own profile's server.
  for (const [name, c] of Object.entries(cases[cardName] || {})) {
    jobs.push([`${cardName}/mcp/${name}`, mcpHarness(card, c.result)]);
    jobs.push([
      `${cardName}/openai/${name}`,
      openaiCard(card, c.input || {}, c.result),
    ]);
  }
}
const out = {};
const results = await Promise.all(jobs.map(([, html]) => run(html)));
jobs.forEach(([key], i) => (out[key] = results[i]));
console.log(JSON.stringify(out));
await browser.close();
