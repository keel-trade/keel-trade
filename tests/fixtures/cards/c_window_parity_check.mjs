/* The window display-parity guard's CARD arms (spec 03 §2.5, guard 12).
 *
 * Renders the backtest card and the compare card in real Chromium from the
 * shared parity fixture (`tests/fixtures/window-parity.fixture.json`, a
 * byte-identical copy of keel-app's `window-parity.fixture.json`) and reads
 * the range each one SHOWS, plus the adapter's own `fmtWindow` for the
 * fixture's second run (a served `last_bar` earlier than end − 1 day).
 *
 * Prints ONE JSON object on stdout; `tests/test_window_parity.py` makes the
 * assertions.
 *
 * Usage: node c_window_parity_check.mjs --cards <dir> --fixture <file>
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
const fixturePath = arg("fixture");
if (!cardsDir || !fixturePath) {
  console.error(
    "usage: c_window_parity_check.mjs --cards <dir> --fixture <file>",
  );
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));

function harness(card, envelope) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope).replace(/<\//g, "<\\/") +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:640px}iframe{border:0;width:100%;display:block}
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
     params:{content:[{type:"text",text:"(markdown)"}],structuredContent:window.__ENV__}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const METRICS = {
  total_return_pct: 41.2,
  max_drawdown_pct: -20.1,
  sharpe: 1.1,
  win_rate_pct: 52.0,
  round_trips: 140,
};

const backtestEnv = {
  view: {
    kind: "backtest",
    size: "evidence",
    name: "Window parity",
    version: 3,
    status: "completed",
    window: fixture.view_window,
    metrics: METRICS,
    net_of: ["fees", "slippage", "carry"],
    tiles: [],
    more_tiles: [],
    markdown: "(markdown)",
  },
};

const run = (label) => ({
  label,
  version: 3,
  status: "completed",
  metrics: METRICS,
  window: fixture.view_window,
});
const compareEnv = {
  view: {
    kind: "comparison",
    size: "comparison",
    name: "Window parity",
    window: fixture.view_window,
    runs: [run("v3"), run("v4")],
    baseline: 0,
    deltas: [null, {}],
    warnings: [],
    notes: [],
    rows: [{ key: "sharpe", label: "Sharpe" }],
    markdown: "(markdown)",
  },
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function shown(kind, envelope, probe) {
  const page = await browser.newPage({
    viewport: { width: 700, height: 1400 },
  });
  await page.setContent(harness(cardHtml(kind), envelope));
  await page.waitForTimeout(700);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const out = await frame.evaluate(probe);
  await page.close();
  return out;
}

const bodyText = () => (document.body.innerText || "").replace(/\s+/g, " ");
const served = fixture.served_last_bar.run.window.ran;

const result = {
  expected: fixture.expected,
  servedExpected: fixture.served_last_bar.expected,
  backtest: await shown("backtest", backtestEnv, bodyText),
  compare: await shown("compare", compareEnv, bodyText),
};
// The chart's readout names the same last day as the range (Q-1883): a
// curve whose last mark sits at the window's EXCLUSIVE end reads the last
// covered day, and — the control — a curve ending inside the window keeps
// its own date.
function curveEnding(lastIso) {
  const end = Date.parse(lastIso);
  const pts = [];
  for (let i = 0; i < 40; i++) {
    const t = new Date(end - (39 - i) * 7 * 86400000).toISOString();
    pts.push([t, 10000 + i * 50, -(i % 5)]);
  }
  return { points: pts };
}
const readout = () => {
  const r = document.querySelector(".readout");
  return {
    readout: r ? r.innerText.trim() : null,
    body: (document.body.innerText || "").replace(/\s+/g, " "),
  };
};
result.chartAtEnd = await shown(
  "backtest",
  {
    ...backtestEnv,
    curve: curveEnding(fixture.view_window.end + "T00:00:00Z"),
  },
  readout,
);
result.chartInside = await shown(
  "backtest",
  { ...backtestEnv, curve: curveEnding("2026-09-10T00:00:00Z") },
  readout,
);
// The adapter's own rule, over the fixture's second run (a served last_bar
// two days before end − 1): it must READ last_bar, not re-derive it — and,
// the control, re-derive end − 1 day when no last_bar is served.
{
  const page = await browser.newPage();
  await page.setContent(harness(cardHtml("backtest"), backtestEnv));
  await page.waitForTimeout(500);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  result.servedRange = await frame.evaluate(
    (w) => {
      const text = window.KeelHost.fmtWindow(w);
      return text ? text.split(" · ")[0] : null;
    },
    {
      start: served.start,
      end: served.end_exclusive,
      last_bar: served.last_bar,
    },
  );
  result.derivedRange = await frame.evaluate(
    (w) => {
      const text = window.KeelHost.fmtWindow(w);
      return text ? text.split(" · ")[0] : null;
    },
    { start: served.start, end: served.end_exclusive },
  );
  await page.close();
}
await browser.close();
process.stdout.write(JSON.stringify(result));
