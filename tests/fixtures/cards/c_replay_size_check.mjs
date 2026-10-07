/* The replay-size guard (Q-1770).
 *
 * Reopening a claude.ai conversation re-mounts every card. The host's
 * frames start at 150 px and it is not yet listening for
 * `ui/notifications/size-changed` when the card's first posts go out —
 * and the adapter deduplicated every later post of the same size, so a
 * full card stayed clipped at 150 px until an unrelated resize.
 *
 * This host models that: it delivers the result in the `ui/initialize`
 * response (the replay shape — the result already exists), and IGNORES
 * every size message until `listenAfter` ms after `initialized`. Only
 * the heights it accepted are recorded; the frame starts at 150 px and
 * changes only on an accepted message, exactly as a host sizes it.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_replay_size.py``
 * makes the assertions.
 *
 * Usage: node c_replay_size_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>]
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
    "usage: c_replay_size_check.mjs --cards <dir> [--fixtures <dir>]",
  );
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

function harness(card, o) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__O__=" +
    JSON.stringify(o).replace(/<\//g, "<\\/") +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:720px}iframe{border:0;width:100%;display:block;height:150px}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
const O=window.__O__;
const f=document.getElementById("c");
window.__ACCEPTED__=[];window.__IGNORED__=0;
let listening=O.listenAfter==null;
function send(m){f.contentWindow.postMessage(m,"*");}
function wrap(env){return {content:[{type:"text",text:JSON.stringify(env)}]};}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){
   const result={protocolVersion:"2026-01-26",hostContext:{theme:"dark",
     displayMode:"inline",availableDisplayModes:["inline"],styles:{variables:{}}}};
   if(O.replay)result.toolResult=wrap(O.first);
   send({jsonrpc:"2.0",id:m.id,result});}
 else if(m.method==="ui/notifications/initialized"){
   if(O.listenAfter!=null)setTimeout(()=>{listening=true;},O.listenAfter);
   if(!O.replay)send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:wrap(O.first)});
   if(O.second)setTimeout(()=>send({jsonrpc:"2.0",
     method:"ui/notifications/tool-result",params:wrap(O.second)}),O.secondAt);}
 else if(m.method==="ui/notifications/size-changed"){
   if(!listening){window.__IGNORED__++;return;}
   window.__ACCEPTED__.push(m.params.height);
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function arm(kind, o, settle) {
  const page = await browser.newPage({
    viewport: { width: 768, height: 1400 },
  });
  await page.setContent(harness(cardHtml(kind), o));
  await page.waitForTimeout(settle);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const content = await frame.evaluate(() => {
    const root = document.getElementById("root");
    return Math.ceil(root.scrollHeight || root.getBoundingClientRect().height);
  });
  const host = await page.evaluate(() => ({
    accepted: window.__ACCEPTED__,
    ignored: window.__IGNORED__,
    frameHeight: Math.round(
      document.getElementById("c").getBoundingClientRect().height,
    ),
  }));
  await page.close();
  return Object.assign({ content }, host);
}

const evidence = readFixture("c_backtest.envelope.json");
const receipt = readFixture("c_bt_receipt_completed.envelope.json");
const strategy = readFixture("strategy_hrp.envelope.json");

const runs = {
  // The founder's case: a full summarize card replayed; the host starts
  // listening 600 ms after the handshake.
  replayFull: arm(
    "backtest",
    { replay: true, first: evidence, listenAfter: 600 },
    4200,
  ),
  replayReceipt: arm(
    "backtest",
    { replay: true, first: receipt, listenAfter: 600 },
    4200,
  ),
  replayStrategy: arm(
    "strategy",
    { replay: true, first: strategy, listenAfter: 600 },
    4200,
  ),
  // CONTROL: a live host that listens from the start.
  liveFull: arm("backtest", { first: evidence }, 1500),
  // Grow and shrink both re-post: receipt → full, full → receipt.
  grow: arm(
    "backtest",
    { first: receipt, second: evidence, secondAt: 500 },
    2000,
  ),
  shrink: arm(
    "backtest",
    { first: evidence, second: receipt, secondAt: 500 },
    2000,
  ),
};
const out = {};
for (const [k, v] of Object.entries(runs)) out[k] = await v;
console.log(JSON.stringify(out, null, 1));
await browser.close();
