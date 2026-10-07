/* The opened-card guard (Q-1777 …).
 *
 * The founder's claude.ai screenshot of an OPENED compose receipt
 * ("Hide pipeline"): a raw change line under the chips —
 * `Execution · buffer 0.1 → 0.2 | Universe · resolved ["AAVE","ARB",…
 * → —` — two Open links, a divider above the action row, and a name
 * truncated because the header's change chip ate the row. This drives
 * the real cards in real Chromium through the real `ui/initialize` →
 * `tool-result` sequence, opens them the way a person does, and reads
 * what is on screen.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_opened.py``
 * makes the assertions.
 *
 * `--served <file>` adds one arm per entry of `{name: {kind, envelope}}` —
 * envelopes the tests build with the REAL tool handlers (Q-1787), so the
 * card is measured against what the server actually sends.
 *
 * Usage: node c_opened_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>] [--served <file>]
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
const servedFile = arg("served", null);
if (!cardsDir) {
  console.error("usage: c_opened_check.mjs --cards <dir> [--fixtures <dir>]");
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
     theme:"dark",displayMode:"inline",availableDisplayModes:["inline","fullscreen"],styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",
     params:{content:[{type:"text",text:JSON.stringify(window.__ENV__)}]}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const PROBE = () => {
  const vis = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const txt = (el) => ((el && el.innerText) || "").trim();
  const root = document.getElementById("root");
  const q = (s) => Array.from(document.querySelectorAll(s)).filter(vis);
  return {
    text: txt(root),
    summaries: q(".sv-summary").map(txt),
    cfgChips: q(".sv-cfgrow .sv-chip").map((el) => ({
      text: txt(el),
      changed: el.classList.contains("sv-chg"),
    })),
    declChips: q(".sv-decls .sv-sc").map(txt),
    opened: !!document.querySelector(".rr-body"),
    // Every visible way to open this in Keel (Q-1778: one per card).
    opens: q("a, [role=link], .button-link")
      .map(txt)
      .filter((t) => /^Open\b/.test(t)),
    name: (() => {
      const n = document.querySelector(".rr .rr-name");
      if (!n) return null;
      const full = n.getAttribute("data-full") || n.textContent;
      // Cut by CSS (trailing ellipsis) or in the middle (Q-1780).
      const cut = n.scrollWidth > n.clientWidth + 1 || n.textContent !== full;
      return { w: n.clientWidth, sw: n.scrollWidth, cut: cut };
    })(),
    rowChange: q(".rr .rr-sc, .rr .rr-win").map(txt),
    // Rules drawn directly above the actions (Q-1779): the action band and
    // a card's own footer.
    actionRules: q("#link-row, .sv-foot")
      .filter((el) => parseFloat(getComputedStyle(el).borderTopWidth) > 0)
      .map((el) => el.id || el.className),
    actionBands: q("#link-row, .sv-foot").length,
    // The headline numbers as drawn (Q-1781): tile label + value + tone,
    // and the comparison's row labels.
    tiles: q(".stat").map((el) => ({
      label: txt(el.querySelector(".label")),
      // The label is cut (an ellipsis) when it overflows its tile (Q-1799).
      cut: (() => {
        const l = el.querySelector(".label");
        return l.scrollWidth > l.clientWidth + 1;
      })(),
      value: txt(el.querySelector(".value")),
      tone: (el.querySelector(".value").className || "")
        .replace("value", "")
        .trim(),
    })),
    cmpRows: q("table.cmp td.k").map(txt),
    // The chart's x-axis labels (the bottom-most text row) and its
    // readout (Q-1783).
    xAxis: (() => {
      const ts = Array.from(document.querySelectorAll(".chart svg text"));
      if (!ts.length) return [];
      const yMax = Math.max(...ts.map((t) => Number(t.getAttribute("y"))));
      return ts
        .filter((t) => Number(t.getAttribute("y")) === yMax)
        .sort(
          (a, b) => Number(a.getAttribute("x")) - Number(b.getAttribute("x")),
        )
        .map((t) => t.textContent);
    })(),
    readout: txt(document.querySelector(".chart .readout, .readout")),
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(kind, envelope, opts) {
  const o = opts || {};
  const width = o.width || 720;
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 1400 },
  });
  await page.setContent(harness(cardHtml(kind), envelope));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForTimeout(600);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  if (o.open) {
    await frame.evaluate(() => {
      const b = document.querySelector(".rr .rr-act[aria-expanded]");
      if (b) b.click();
    });
    await page.waitForTimeout(400);
  }
  const m = await frame.evaluate(PROBE);
  await page.close();
  return m;
}

/* The founder's save: an Execution buffer edit, with the universe's
 * resolved list reported alongside it (the server lane is removing that
 * report; the card must draw it sanely while it exists). */
const SYMS = readFixture("strategy_declaration_change.envelope.json").view
  .header.universe.symbols;
function founderSave(size) {
  const env = readFixture("strategy_declaration_receipt.envelope.json");
  env.view.size = size;
  env.view.change.declarations = {
    execution: { buffer_threshold: { a: 0.1, b: 0.2 } },
    universe: { resolved: { a: SYMS, b: null } },
  };
  env.view.change.summary_text =
    "Execution · buffer 0.1 → 0.2 | Universe · resolved " +
    JSON.stringify(SYMS) +
    " → —";
  return env;
}

const runs = {
  openedSave: measure("strategy", founderSave("receipt"), { open: true }),
  changeCard: measure("strategy", founderSave("change")),
  // CONTROL: a save that changed no declaration keeps plain chips.
  openedPlain: measure(
    "strategy",
    readFixture("strategy_receipt.envelope.json"),
    { open: true },
  ),
};
/* The founder's name was "Simple Momentum (Top 30 …" — truncated at full
 * desktop width because the row's change chip ate it. */
const LONG = "Simple Momentum (Top 30 by volume, weekly rebalance)";
function longSave() {
  const env = founderSave("receipt");
  env.view.name = LONG;
  return env;
}
Object.assign(runs, {
  closedLong: measure("strategy", longSave(), {}),
  openedLong: measure("strategy", longSave(), { open: true }),
  openedRun: measure(
    "backtest",
    readFixture("c_bt_receipt_completed.envelope.json"),
    { open: true },
  ),
  closedRun: measure(
    "backtest",
    readFixture("c_bt_receipt_completed.envelope.json"),
    {},
  ),
});
/* Every card kind in its full state, for the action-band rule (Q-1779). */
Object.assign(runs, {
  fullBacktest: measure(
    "backtest",
    readFixture("c_backtest.envelope.json"),
    {},
  ),
  fullCompare: measure("compare", readFixture("compare_4.envelope.json"), {}),
  fullStrategy: measure(
    "strategy",
    readFixture("strategy_hrp.envelope.json"),
    {},
  ),
  fullLive: measure("live", readFixture("c_live_overview.envelope.json"), {}),
});
/* Envelopes built by the real tool handlers (Q-1787). */
const served = servedFile
  ? JSON.parse(fs.readFileSync(servedFile, "utf8"))
  : {};
for (const [name, s] of Object.entries(served)) {
  runs[name] = measure(s.kind, s.envelope, { width: s.width || 720 });
}
const out = { symbols: SYMS.length, long: LONG };
for (const [k, v] of Object.entries(runs)) out[k] = await v;
console.log(JSON.stringify(out, null, 1));
await browser.close();
