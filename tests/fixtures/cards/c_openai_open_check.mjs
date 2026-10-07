/* The ChatGPT open-a-receipt guard (Q-1774).
 *
 * On ChatGPT a human click on "Show result" / "Show pipeline" did nothing
 * visible: the host sizes the widget frame itself, so the in-place open
 * grew a document the frame never grew to show. This drives the real
 * cards in real Chromium with `window.openai` spliced in before the
 * adapter runs — recording every `requestDisplayMode` and `openExternal`
 * call — clicks the disclosure, and reads what happened. An MCP Apps
 * control arm proves the in-place open there is untouched.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_openai_open.py``
 * makes the assertions.
 *
 * Usage: node c_openai_open_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>]
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
  console.error(
    "usage: c_openai_open_check.mjs --cards <dir> [--fixtures <dir>]",
  );
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

/* `grant`: the mode the host answers with, or null for a host with no
 * requestDisplayMode at all. Granting fullscreen also flips the host's
 * own `displayMode` global, as ChatGPT does. */
function openaiCard(card, envelope, grant, toolInput) {
  const rdm =
    grant === null
      ? ""
      : "requestDisplayMode:function(a){window.__CALLS__.push(['rdm',a]);" +
        "if(" +
        JSON.stringify(grant) +
        "==='fullscreen'){window.openai.displayMode='fullscreen';}" +
        "return Promise.resolve({mode:" +
        JSON.stringify(grant) +
        "})},";
  const shim =
    "<script>window.__CALLS__=[];window.openai={theme:'dark',displayMode:'inline'," +
    "maxHeight:640,callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(a){window.__CALLS__.push(['ext',a])}," +
    "sendFollowUpMessage:function(){}," +
    rdm +
    "notifyIntrinsicHeight:function(h){window.__CALLS__.push(['height',h])}," +
    "toolInput:" +
    JSON.stringify(toolInput || { strategy_id: "str_x" }) +
    ",toolOutput:" +
    JSON.stringify(envelope).replace(/<\//g, "<\\/") +
    "};</script>";
  return card.replace(/<body([^>]*)>/, (_m, attrs) => `<body${attrs}>${shim}`);
}

function mcpHarness(card, envelope) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope).replace(/<\//g, "<\\/") +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:720px}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
window.__SENT__=[];
const f=document.getElementById("c");
function send(m){f.contentWindow.postMessage(m,"*");}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 window.__SENT__.push(m.method);
 if(m.method==="ui/initialize"){
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{
     theme:"dark",displayMode:"inline",availableDisplayModes:["inline","fullscreen"],styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",
     params:{content:[{type:"text",text:JSON.stringify(window.__ENV__)}]}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const PROBE = () => ({
  // Visible wordmarks, in any state (Q-1776).
  brands: Array.from(document.querySelectorAll(".brand, .sv-brand")).filter(
    (el) => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return cs.display !== "none" && !el.hidden && r.width > 0;
    },
  ).length,
  mode: document.documentElement.getAttribute("data-display-mode"),
  receiptRows: document.querySelectorAll(".rr").length,
  opened: !!document.querySelector(".rr-body"),
  tiles: document.querySelectorAll(".stat").length,
  blocks: document.querySelectorAll(".sv-blk").length,
  calls: window.__CALLS__ || null,
  text: (document.getElementById("root").innerText || "").slice(0, 200),
});

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function click(html, mcp) {
  const page = await browser.newPage({
    viewport: { width: 768, height: 1000 },
  });
  await page.setContent(html);
  await page.waitForTimeout(500);
  const frame =
    page.frames().find((fr) => fr !== page.mainFrame()) || page.mainFrame();
  const before = await frame.evaluate(PROBE);
  await frame.evaluate(() => {
    const b = document.querySelector(".rr .rr-act[aria-expanded]");
    if (b) b.click();
  });
  await page.waitForTimeout(500);
  const after = await frame.evaluate(PROBE);
  if (mcp) after.sent = await page.evaluate(() => window.__SENT__);
  await page.close();
  return { before, after };
}

const bt = readFixture("c_bt_receipt_completed.envelope.json");
const st = readFixture("strategy_receipt.envelope.json");
const full = readFixture("strategy_hrp.envelope.json");
const runs = {
  grantBacktest: click(openaiCard(cardHtml("backtest"), bt, "fullscreen")),
  grantStrategy: click(openaiCard(cardHtml("strategy"), st, "fullscreen")),
  denyBacktest: click(openaiCard(cardHtml("backtest"), bt, "inline")),
  noApiBacktest: click(openaiCard(cardHtml("backtest"), bt, null)),
  mcpBacktest: click(mcpHarness(cardHtml("backtest"), bt), true),
  // Wordmark arms (Q-1776): in flight (no output yet) and a full card.
  openaiInflight: click(openaiCard(cardHtml("backtest"), null, "fullscreen")),
  // In flight as a VIEW: the full skeleton, under the template's own head.
  openaiInflightView: click(
    openaiCard(cardHtml("backtest"), null, "fullscreen", {
      strategy_id: "str_x",
      present: "view",
    }),
  ),
  openaiStrategyFull: click(
    openaiCard(cardHtml("strategy"), full, "fullscreen"),
  ),
  mcpStrategyFull: click(mcpHarness(cardHtml("strategy"), full), true),
};
const out = { btUrl: bt.hero_url || null };
for (const [k, v] of Object.entries(runs)) out[k] = await v;
console.log(JSON.stringify(out, null, 1));
await browser.close();
