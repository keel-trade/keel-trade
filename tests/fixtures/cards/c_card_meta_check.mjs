/* The card merges `_meta["keel/card"]` over `structuredContent` (spec 02
 * §3, guard G11).
 *
 * Renders the backtest card from a result whose curve rides ONLY in
 * `_meta["keel/card"]` — on both dialects (MCP Apps: the tool-result
 * notification's `_meta`; ChatGPT: `window.openai.toolResponseMetadata`) —
 * and, as the control, the same result with no `_meta`. Reads whether the
 * chart was drawn and how many points it was given.
 *
 * Prints ONE JSON object on stdout; `tests/test_card_meta_merge.py` makes
 * the assertions.
 *
 * Usage: node c_card_meta_check.mjs --cards <dir> [--result <tool-result.json>]
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
if (!cardsDir) {
  console.error("usage: c_card_meta_check.mjs --cards <dir>");
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");

const POINTS = 240;
const series = [];
for (let i = 0; i <= POINTS; i++) {
  const day = new Date(Date.UTC(2024, 7, 19) + i * 3 * 86400000)
    .toISOString()
    .slice(0, 10);
  series.push([day + "T00:00:00Z", 10000 + i * 7, -((i % 17) * 0.4)]);
}

const structured = {
  view: {
    kind: "backtest",
    size: "evidence",
    name: "Meta merge",
    version: 2,
    status: "completed",
    window: { start: "2024-08-19", end: "2026-09-23", last_bar: "2026-09-22" },
    metrics: { total_return_pct: 12.1, max_drawdown_pct: -8.2, sharpe: 0.9 },
    net_of: ["fees", "slippage", "carry"],
    markdown: "(markdown)",
  },
};
const card = {
  curve: { points: series, start: series[0][0], end: series[POINTS][0] },
  view: { tiles: [], more_tiles: [] },
};

function mcpHarness(html, result) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(html).replace(/<\//g, "<\\/") +
    ";window.__RESULT__=" +
    JSON.stringify(result).replace(/<\//g, "<\\/") +
    ";";
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
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

function openaiCard(html, toolOutput, meta, freeze) {
  const shim =
    "<script>window.openai={theme:'dark',displayMode:'inline'," +
    "maxHeight:640,callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(){},sendFollowUpMessage:function(){}," +
    "requestDisplayMode:function(){return Promise.resolve({mode:'inline'})}," +
    "notifyIntrinsicHeight:function(){},toolInput:{}," +
    "toolOutput:" +
    JSON.stringify(toolOutput).replace(/<\//g, "<\\/") +
    ",toolResponseMetadata:" +
    JSON.stringify(meta).replace(/<\//g, "<\\/") +
    "};" +
    // A host may hand the card FROZEN objects; an in-place merge then
    // throws under "use strict" and the card draws nothing.
    (freeze
      ? "(function f(o){if(o&&typeof o==='object'){Object.freeze(o);" +
        "Object.keys(o).forEach(function(k){f(o[k]);});}})" +
        "(window.openai.toolOutput);" +
        "(function f(o){if(o&&typeof o==='object'){Object.freeze(o);" +
        "Object.keys(o).forEach(function(k){f(o[k]);});}})" +
        "(window.openai.toolResponseMetadata);"
      : "") +
    "</script>";
  // A replacer FUNCTION: a replacement string would read `$1` inside the
  // shim as a capture group, and a real result says "Fees paid $1,244".
  return html.replace(/<body([^>]*)>/, (_m, attrs) => `<body${attrs}>${shim}`);
}

const PROBE = () => {
  const chart = document.querySelector(".chart svg");
  return {
    chart: !!chart,
    label: chart ? chart.getAttribute("aria-label") : null,
    text: (document.body.innerText || "").replace(/\s+/g, " "),
    // The host's own objects are never written to: the merge works on a
    // copy (null where the dialect has no such global).
    hostOutputUntouched: window.openai
      ? !("curve" in window.openai.toolOutput)
      : null,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function run(html, direct) {
  const page = await browser.newPage({
    viewport: { width: 768, height: 1200 },
  });
  await page.setContent(html);
  await page.waitForTimeout(700);
  const frame = direct
    ? page.mainFrame()
    : page.frames().find((fr) => fr !== page.mainFrame());
  const out = await frame.evaluate(PROBE);
  await page.close();
  return out;
}

const html = cardHtml("backtest");
const result = {
  points: POINTS + 1,
  mcpWithMeta: await run(
    mcpHarness(html, {
      content: [{ type: "text", text: "(markdown)" }],
      structuredContent: structured,
      _meta: { "keel/card": card },
    }),
  ),
  mcpWithoutMeta: await run(
    mcpHarness(html, {
      content: [{ type: "text", text: "(markdown)" }],
      structuredContent: structured,
    }),
  ),
  openaiWithMeta: await run(
    openaiCard(html, structured, { "keel/card": card }),
    true,
  ),
  openaiFrozen: await run(
    openaiCard(html, structured, { "keel/card": card }, true),
    true,
  ),
};

// `--result <file>`: a REAL tool result — the channels the adapter emitted
// for a replayed recording (`tests/test_channel_budgets.py`, Q-1894) —
// drawn on both dialects, plus the same result with its `_meta` removed
// (the control: the chart must come from `_meta`, not `structuredContent`).
const replayPath = arg("result");
if (replayPath) {
  const replayed = JSON.parse(fs.readFileSync(replayPath, "utf8"));
  const replayCard = (replayed._meta || {})["keel/card"] || {};
  const noMeta = { ...replayed };
  delete noMeta._meta;
  result.replay = {
    metaPoints: ((replayCard.curve || {}).points || []).length,
    mcp: await run(mcpHarness(html, replayed)),
    openai: await run(
      openaiCard(html, replayed.structuredContent, replayed._meta || {}),
      true,
    ),
    mcpWithoutMeta: await run(mcpHarness(html, noMeta)),
  };
}
await browser.close();
process.stdout.write(JSON.stringify(result));
