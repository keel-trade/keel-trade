/* The error / empty-result guard (Q-1773).
 *
 * On ChatGPT a tool ERROR drew the empty card — "Untitled", four dashed
 * tiles, "+5 more", "Ask for the Keel link to view this in the app." —
 * because `window.openai.toolOutput` is the result's `structuredContent`,
 * and an error envelope carried none: the card was handed `{}`. This
 * drives the real cards in real Chromium through BOTH dialects:
 *
 *   openai  — `window.openai` spliced in before the adapter runs, with
 *             `toolOutput` set to what ChatGPT delivers;
 *   mcp     — the real `ui/initialize` → `tool-result` sequence, with the
 *             CallToolResult the host forwards.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_result_states.py``
 * makes the assertions.
 *
 * `--adapter <file>` adds one ChatGPT arm per entry of a JSON object
 * `{name: {kind, toolInput, toolOutput}}` — the tests build `toolOutput`
 * from what the REAL MCP server returned (Q-1785), so the arm proves the
 * server/card contract rather than a hand-written envelope.
 *
 * Usage: node c_result_states_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>] [--adapter <file>]
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
const adapterFile = arg("adapter", null);
if (!cardsDir) {
  console.error(
    "usage: c_result_states_check.mjs --cards <dir> [--fixtures <dir>]",
  );
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

/* MCP Apps host: forwards `result` verbatim as the tool-result params. */
function mcpHarness(card, result) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
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

/* ChatGPT: `window.openai` exists before the adapter runs. */
function openaiCard(card, toolInput, toolOutput) {
  const shim =
    "<script>window.openai={theme:'dark',displayMode:'inline'," +
    "maxHeight:640,callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(){},sendFollowUpMessage:function(){}," +
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
  return {
    text: (root.innerText || "").trim(),
    tiles: document.querySelectorAll(".stat").length,
    skeleton: !!document.querySelector(".skeleton"),
    errLine: vis(document.querySelector(".err-line")),
    linkRowVisible: vis(document.getElementById("link-row")),
    height: Math.round(root.getBoundingClientRect().height),
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
  // The ChatGPT arms load the card as the page itself (no host frame).
  const frame =
    page.frames().find((fr) => fr !== page.mainFrame()) || page.mainFrame();
  const m = await frame.evaluate(PROBE);
  await page.close();
  return m;
}

const bt = readFixture("c_backtest.envelope.json");
const cmpErr = readFixture("compare_error.envelope.json");
// The error envelopes the server serialises (spec §13.5 envelope_error).
const runErr = {
  code: "invalid_date_range",
  message: "start_date 2025-06-01 is after end_date 2025-01-01.",
  what_was_expected: "start_date on or before end_date.",
  example: {},
  suggested_next_action: {},
};
const stratErr = {
  code: "not_found",
  message: "Strategy str_nope was not found.",
  what_was_expected: "An existing strategy id.",
  example: {},
  suggested_next_action: {},
};
// keel-api's own phrasing (services/keel-api/src/utils/backtest_window.py,
// WINDOW_INVERTED): a failure preamble the card's lead already says (Q-1800).
const leadErr = {
  code: "WINDOW_INVERTED",
  message:
    "Cannot backtest — start_date 2025-06-01 is after end_date 2025-01-01. " +
    "Swap them to run 2025-01-01 to 2025-06-01.",
  what_was_expected: "",
  example: {},
  suggested_next_action: {},
};
const leadStratErr = {
  ...stratErr,
  message: "Cannot read strategy — Strategy str_nope was not found.",
};
const wrapText = (obj) => ({
  content: [{ type: "text", text: JSON.stringify(obj) }],
});

const arms = {
  // ChatGPT today: the error result has no structuredContent → `{}`.
  openaiEmptyBacktest: run(
    openaiCard(cardHtml("backtest"), { strategy_id: "str_x" }, {}),
  ),
  openaiEmptyCompare: run(
    openaiCard(
      cardHtml("compare"),
      { backtest_ids: ["btr_doesnotexist123"] },
      {},
    ),
  ),
  openaiEmptyStrategy: run(
    openaiCard(cardHtml("strategy"), { strategy_id: "str_nope" }, {}),
  ),
  // ChatGPT once the server ships the envelope as structuredContent.
  openaiErrorBacktest: run(
    openaiCard(cardHtml("backtest"), { strategy_id: "str_x" }, runErr),
  ),
  openaiErrorStrategy: run(
    openaiCard(cardHtml("strategy"), { strategy_id: "str_nope" }, stratErr),
  ),
  // MCP Apps: the error envelope in the text block (what claude.ai gets).
  mcpErrorBacktest: run(mcpHarness(cardHtml("backtest"), wrapText(runErr))),
  mcpErrorCompare: run(mcpHarness(cardHtml("compare"), wrapText(cmpErr))),
  mcpErrorStrategy: run(mcpHarness(cardHtml("strategy"), wrapText(stratErr))),
  // MCP Apps: a framework-level failure — isError with plain text.
  mcpIsErrorText: run(
    mcpHarness(cardHtml("strategy"), {
      isError: true,
      content: [
        { type: "text", text: "Error executing tool keel_strategy_get: boom" },
      ],
    }),
  ),
  // A server message that carries its own failure lead (Q-1800).
  openaiLeadBacktest: run(
    openaiCard(cardHtml("backtest"), { strategy_id: "str_x" }, leadErr),
  ),
  mcpLeadStrategy: run(
    mcpHarness(cardHtml("strategy"), wrapText(leadStratErr)),
  ),
  // CONTROL: a real result renders as a result on both dialects.
  openaiResult: run(
    openaiCard(cardHtml("backtest"), { backtest_id: "btr_1" }, bt),
  ),
  mcpResult: run(mcpHarness(cardHtml("backtest"), wrapText(bt))),
};
const adapterArms = adapterFile
  ? JSON.parse(fs.readFileSync(adapterFile, "utf8"))
  : {};
const adapterRuns = {};
for (const [name, a] of Object.entries(adapterArms)) {
  adapterRuns[name] = run(
    openaiCard(cardHtml(a.kind), a.toolInput || {}, a.toolOutput),
  );
}
const out = {
  messages: { runErr: runErr.message, stratErr: stratErr.message },
};
for (const [k, v] of Object.entries(arms)) out[k] = await v;
out.adapter = {};
for (const [k, v] of Object.entries(adapterRuns)) out.adapter[k] = await v;
out.btName = bt.strategy_name;
out.cmpErrMessage =
  (cmpErr.view && cmpErr.view.error) || cmpErr.message || null;
console.log(JSON.stringify(out, null, 1));
await browser.close();
