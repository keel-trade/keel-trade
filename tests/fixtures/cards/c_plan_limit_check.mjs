/* The plan-limit state (Q-1806).
 *
 * At 50 of 50 on ChatGPT the backtest card drew its RED error line with
 * the handoff envelope's `message`, which ended in a directive written for
 * the model ("Report the numbers in `example` … do not retry"). This drives
 * the real cards in real Chromium through BOTH dialects with the envelope
 * the real SDK path produced (`--envelope <file>`, written by
 * ``tests/test_widgets_plan_limit.py``), clicks the state's one link, and
 * records what the host was asked to open.
 *
 * Arms:
 *   openaiLimit / mcpLimit   — the plan-limit envelope, both dialects;
 *   openaiError              — CONTROL: a plain error envelope still draws
 *                              the red error line (the scan can tell them
 *                              apart);
 *   openaiLimitCompare       — the same envelope on another card kind (the
 *                              state is the adapter's, not one card's).
 *
 * Prints ONE JSON object on stdout.
 *
 * Usage: node c_plan_limit_check.mjs --cards <dir of {kind}.html> --envelope <file>
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
const envelopeFile = arg("envelope");
if (!cardsDir || !envelopeFile) {
  console.error(
    "usage: c_plan_limit_check.mjs --cards <dir> --envelope <file>",
  );
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const envelope = JSON.parse(fs.readFileSync(envelopeFile, "utf8"));

/* MCP Apps host: forwards `result` verbatim; records `ui/open-link`. */
function mcpHarness(card, result) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__RESULT__=" +
    JSON.stringify(result).replace(/<\//g, "<\\/") +
    ";window.__opened=[];";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:720px}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
const f=document.getElementById("c");
function send(m){f.contentWindow.postMessage(m,"*");}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{
     theme:"dark",displayMode:"inline",availableDisplayModes:["inline"],styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:window.__RESULT__});}
 else if(m.method==="ui/open-link"){
   window.__opened.push(m.params&&m.params.url);
   send({jsonrpc:"2.0",id:m.id,result:{}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

/* ChatGPT: `window.openai` exists before the adapter runs. */
function openaiCard(card, toolInput, toolOutput) {
  const shim =
    "<script>window.__opened=[];window.openai={theme:'dark',displayMode:'inline'," +
    "maxHeight:640,callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(o){window.__opened.push(o&&o.href)}," +
    "sendFollowUpMessage:function(){}," +
    "requestDisplayMode:function(){return Promise.resolve({mode:'inline'})}," +
    "notifyIntrinsicHeight:function(){}," +
    "toolInput:" +
    JSON.stringify(toolInput) +
    ",toolOutput:" +
    JSON.stringify(toolOutput).replace(/<\//g, "<\\/") +
    "};</script>";
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
  const root = document.getElementById("root");
  const headline = document.querySelector(".pl-headline");
  const link = document.querySelector(".plan-limit a.pl-link");
  const errMsg = document.querySelector(".err-line .msg");
  return {
    text: (root.innerText || "").trim(),
    planLimit: vis(document.querySelector(".plan-limit")),
    errLine: vis(document.querySelector(".err-line")),
    headlineColor: headline ? getComputedStyle(headline).color : null,
    errColor: errMsg ? getComputedStyle(errMsg).color : null,
    linkLabel: link ? link.textContent : null,
    linkHref: link ? link.getAttribute("href") : null,
    links: document.querySelectorAll(".plan-limit a").length,
    linkRowVisible: vis(document.getElementById("link-row")),
    tiles: document.querySelectorAll(".stat").length,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function run(html, { click = false } = {}) {
  const page = await browser.newPage({
    viewport: { width: 768, height: 1000 },
  });
  await page.setContent(html);
  await page.waitForTimeout(700);
  const frame =
    page.frames().find((fr) => fr !== page.mainFrame()) || page.mainFrame();
  const m = await frame.evaluate(PROBE);
  if (click && m.linkHref) {
    await frame.click(".plan-limit a.pl-link");
    await page.waitForTimeout(200);
    // The ChatGPT shim records in the card's own window; the MCP host
    // records in the page that hosts the iframe.
    m.opened = await page.mainFrame().evaluate(() => window.__opened || []);
    if (frame !== page.mainFrame() && !m.opened.length) {
      m.opened = await frame.evaluate(() => window.__opened || []);
    }
  }
  await page.close();
  return m;
}

const plainErr = {
  code: "invalid_date_range",
  message: "start_date 2025-06-01 is after end_date 2025-01-01.",
  what_was_expected: "start_date on or before end_date.",
  example: {},
  suggested_next_action: {},
};
const wrapText = (obj) => ({
  content: [{ type: "text", text: JSON.stringify(obj) }],
  structuredContent: obj,
});

const arms = {
  openaiLimit: run(
    openaiCard(cardHtml("backtest"), { strategy_id: "str_x" }, envelope),
    { click: true },
  ),
  mcpLimit: run(mcpHarness(cardHtml("backtest"), wrapText(envelope)), {
    click: true,
  }),
  openaiLimitCompare: run(
    openaiCard(cardHtml("compare"), { backtest_ids: ["btr_1"] }, envelope),
  ),
  openaiError: run(
    openaiCard(cardHtml("backtest"), { strategy_id: "str_x" }, plainErr),
  ),
};
const out = {};
for (const [k, v] of Object.entries(arms)) out[k] = await v;
console.log(JSON.stringify(out, null, 1));
await browser.close();
