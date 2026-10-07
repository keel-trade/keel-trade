/* The render-cadence card guard (BUILD §4.1–§4.4, §2.9; Q-1688/Q-1689).
 *
 * Every rule this lane added is a rule about the RENDERED DOM and its
 * REAL LAYOUT: a receipt is one row under 64 px, a closed receipt draws
 * no action band, opening one in place sends a second `size-changed`,
 * a comparison of eight runs does not draw eight inline columns, two
 * frames from one origin elect a winner. None of that can be asserted
 * on the served JS source, so this drives the real cards through the
 * real `ui/initialize` → `tool-result` sequence in real Chromium and
 * measures what a person would see.
 *
 * Prints ONE JSON object of measurements on stdout;
 * ``tests/test_widgets_cadence.py`` makes the assertions.
 *
 * The supersession arm needs a REAL origin — two `srcdoc` iframes
 * inherit the embedder's origin, and `BroadcastChannel` is inert on the
 * opaque origin `page.setContent` gives you. So this script serves its
 * harness over loopback HTTP for that arm, and only that arm.
 *
 * Usage: node c_cadence_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>] [--shots <dir>]
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

// The ONE host context both screenshot harnesses capture under
// (Q-1718) — `scripts/card_host_context.mjs`. This file and
// `scripts/card_preview.mjs` each used to declare their own, and
// they disagreed about `availableDisplayModes` (Expand drawn or
// not) and about three style variables (`--good` / `--bad` from
// the host or from card.css), so their PNGs could not be compared.
import {
  DISPLAY_MODES,
  HOST_GROUND,
  hostContext,
} from "../../../scripts/card_host_context.mjs";

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
const shotsDir = arg("shots", null);
if (!cardsDir) {
  console.error("usage: c_cadence_check.mjs --cards <dir> [--fixtures <dir>]");
  process.exit(2);
}
if (shotsDir) fs.mkdirSync(shotsDir, { recursive: true });

const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

/* The ChatGPT dialect is detected from `window.openai`, which must
 * exist BEFORE the adapter runs — so it is spliced into the card's own
 * document rather than set from outside. */
function asOpenAI(card, envelope, theme) {
  const shim =
    "<script>window.openai={theme:" +
    JSON.stringify(theme) +
    ",displayMode:'inline',availableDisplayModes:" +
    JSON.stringify(DISPLAY_MODES) +
    "," +
    "maxHeight:640,callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(){},sendFollowUpMessage:function(){}," +
    "requestDisplayMode:function(){return Promise.resolve({mode:'inline'})}," +
    "notifyIntrinsicHeight:function(h){parent.postMessage({jsonrpc:'2.0'," +
    "method:'ui/notifications/size-changed',params:{height:h}},'*')}," +
    "toolOutput:" +
    JSON.stringify(envelope).replace(/<\//g, "<\\/") +
    "};</script>";
  return card.replace(/<body([^>]*)>/, (_m, attrs) => `<body${attrs}>${shim}`);
}

function harness(card, envelope, opts) {
  const o = opts || {};
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope) +
    ";window.__OPTS__=" +
    JSON.stringify({
      deliver: o.deliver !== false,
      ctx: hostContext({
        theme: o.theme,
        displayMode: o.displayMode,
        modes: o.modes,
      }),
    }) +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0;background:${HOST_GROUND[o.theme === "light" ? "light" : "dark"]}}
.frame{width:var(--w)}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
window.__SENT__=[];
const O=window.__OPTS__;
const f=document.getElementById("c");
function send(m){f.contentWindow.postMessage(m,"*");}
function result(env){return {jsonrpc:"2.0",method:"ui/notifications/tool-result",
  params:{content:[{type:"text",text:JSON.stringify(env)}]}};}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 window.__SENT__.push(m);
 if(m.method==="ui/initialize"){
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",
     hostContext:O.ctx}});}
 else if(m.method==="ui/notifications/initialized"){
   if(O.deliver)send(result(window.__ENV__));
   else document.body.setAttribute("data-h","0");}
 else if(m.method==="ui/request-display-mode"){
   send({jsonrpc:"2.0",id:m.id,result:{mode:m.params.mode}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";
   document.body.setAttribute("data-h",m.params.height);}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const { chromium } = require("playwright");
const browser = await chromium.launch();

/* Read off the live DOM everything a cadence rule is about. */
const PROBE = () => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const txt = (el) => ((el && el.textContent) || "").trim();
  const vis = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const root = document.getElementById("root") || document.body;
  const rr = document.querySelector(".rr");
  const disclosure = q(".rr .rr-act[aria-expanded]")[0] || null;
  return {
    height: Math.round(root.getBoundingClientRect().height),
    docHeight: Math.round(
      document.documentElement.getBoundingClientRect().height,
    ),
    rowHeight: rr ? Math.round(rr.getBoundingClientRect().height) : null,
    text: root.innerText || "",
    dataSize: document.body.getAttribute("data-size"),
    ownsLink: document.body.getAttribute("data-owns-link"),
    noHero: document.body.getAttribute("data-no-hero"),
    linkRowHidden: (document.getElementById("link-row") || {}).hidden === true,
    brandVisible: q(".brand").some(vis),
    brands: q(".brand").length,
    rrName: txt(document.querySelector(".rr-name")),
    rrNums: q(".rr-nums span").map(txt),
    rrChips: q(".rr .chip").map(txt),
    rrWindow: txt(document.querySelector(".rr-win")),
    rrErr: txt(document.querySelector(".rr-err")),
    rrHunk: txt(document.querySelector(".rr-sc")),
    rrHunkKey: txt(document.querySelector(".rr-sc .k")),
    rrHunkOld: txt(document.querySelector(".rr-sc .old")),
    rrHunkNew: txt(document.querySelector(".rr-sc .new")),
    rrNote: txt(document.querySelector(".rr-win")),
    mutedRow: txt(document.querySelector("#link-row .muted")),
    rrOpenAnchor: q(".rr a.rr-act").map((el) => ({
      text: txt(el),
      href: el.getAttribute("href"),
      title: el.getAttribute("title"),
      h: Math.round(el.getBoundingClientRect().height),
    })),
    disclosureLabel: disclosure ? txt(disclosure) : null,
    disclosureExpanded: disclosure
      ? disclosure.getAttribute("aria-expanded")
      : null,
    disclosureHeight: disclosure
      ? Math.round(disclosure.getBoundingClientRect().height)
      : null,
    buttonLinks: q("#link-row .button-link").map(txt),
    buttonLinksVisible: q("#link-row .button-link").filter(vis).map(txt),
    svOpenLinks: q(".sv-open-link").length,
    svExpand: q(".sv-expand").length,
    svLinks: q(".sv-link").map(txt),
    svSummaries: q(".sv-summary, .sv-none").map(txt),
    // The declaration hunks of a change card (Q-1707): one context line
    // per section, one `key old → new` chip per declaration that moved.
    svDeclCtx: q(".sv-decls .sv-ctx").map(txt),
    svDecls: q(".sv-decls .sv-sc").map((el) => ({
      key: txt(el.querySelector(".sv-k")),
      old: txt(el.querySelector(".sv-old")),
      now: txt(el.querySelector(".sv-new")),
    })),
    structures: q(".sv-structure").length,
    blocks: q(".sv-blk").length,
    svgs: q("svg").length,
    chartPaths: q(".chart path.ml").length,
    chartDashed: q(".chart path.ml[stroke-dasharray]").length,
    chartStrokes: q(".chart path.ml").map((el) => el.getAttribute("stroke")),
    ddPaths: q(".chart path.mdd").length,
    legend: q(".legend > span").map(txt),
    legendRest: txt(document.querySelector(".legend .rest")),
    tables: q("table.cmp").length,
    cmpCols: q("table.cmp tr").length
      ? q("table.cmp tr")[0].querySelectorAll("th.run").length
      : 0,
    cmpRowKeys: q("table.cmp td.k").map(txt),
    cmpHeaders: q("table.cmp th.run").map(txt),
    cmpAnchors: q("table.cmp td a").map((el) => el.getAttribute("href")),
    cmpDeltas: q("table.cmp td .delta").map(txt),
    rowItems: q(".row-item").length,
    rowLeads: q(".row-item .lead").map(txt),
    notes: q(".note").map(txt),
    moreButtons: q("button.more").map(txt),
    errLine: txt(document.querySelector(".err-line .msg")),
    tiles: q(".stat").length,
    skeletonShapes: q(".skeleton > *")
      .filter(vis)
      .map((el) => el.className),
    overflowX:
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
    internalScroll: q("*").some((el) => {
      const cs = getComputedStyle(el);
      return (
        (cs.overflowY === "auto" || cs.overflowY === "scroll") &&
        el.scrollHeight > el.clientHeight + 1
      );
    }),
  };
};

async function measure(kind, envelope, opts) {
  const o = opts || {};
  const width = o.width || 720;
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 1400 },
    deviceScaleFactor: o.shot ? 2 : 1,
  });
  let card = cardHtml(kind);
  if (o.openai) card = asOpenAI(card, envelope, o.theme || "dark");
  await page.setContent(harness(card, envelope, o));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForFunction(() => document.body.hasAttribute("data-h"), {
    timeout: 20000,
  });
  await page.waitForTimeout(o.settle == null ? 250 : o.settle);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const m = await frame.evaluate(PROBE);
  // The capture is of the state the assertions above describe — taken
  // BEFORE any click, unless the arm asks for the opened state.
  if (o.shot && shotsDir && !o.shotAfter) {
    await page.locator(".frame").screenshot({
      path: path.join(shotsDir, `${o.shot}.png`),
    });
    m.shot = `${o.shot}.png`;
  }
  m.sentSizes = await page.evaluate(
    () =>
      window.__SENT__.filter(
        (x) => x.method === "ui/notifications/size-changed",
      ).length,
  );
  if (o.click) {
    await frame.evaluate((sel) => {
      const b = document.querySelector(sel);
      if (b) b.click();
    }, o.click);
    await page.waitForTimeout(o.clickSettle == null ? 350 : o.clickSettle);
    m.after = await frame.evaluate(PROBE);
    m.after.sentSizes = await page.evaluate(
      () =>
        window.__SENT__.filter(
          (x) => x.method === "ui/notifications/size-changed",
        ).length,
    );
    if (o.click2) {
      await frame.evaluate((sel) => {
        const b = document.querySelector(sel);
        if (b) b.click();
      }, o.click2);
      await page.waitForTimeout(250);
      m.after2 = await frame.evaluate(PROBE);
    }
  }
  if (o.shot && shotsDir && o.shotAfter) {
    await page.locator(".frame").screenshot({
      path: path.join(shotsDir, `${o.shot}.png`),
    });
    m.shot = `${o.shot}.png`;
  }
  await page.close();
  return m;
}

/* Two instances of one connector, on ONE REAL ORIGIN. Returns what each
 * frame rendered after both have announced their election key. */
async function twoFrames(kind, envA, envB) {
  const card = cardHtml(kind);
  const page = `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:720px}iframe{border:0;width:100%;display:block}
</style></head><body>
<div class="frame"><iframe id="a"></iframe></div>
<div class="frame"><iframe id="b"></iframe></div>
<script>
window.__CARD__=${JSON.stringify(card).replace(/<\//g, "<\\/")};
window.__A__=${JSON.stringify(envA)};
window.__B__=${JSON.stringify(envB)};
const seen={a:0,b:0};
function wire(id, env){
  const f=document.getElementById(id);
  function send(m){f.contentWindow.postMessage(m,"*");}
  window.addEventListener("message",(ev)=>{
    if(ev.source!==f.contentWindow)return;
    const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
    if(m.method==="ui/initialize"){
      send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",
        hostContext:${JSON.stringify(hostContext({ theme: "dark" }))}}});}
    else if(m.method==="ui/notifications/initialized"){
      send({jsonrpc:"2.0",method:"ui/notifications/tool-result",
        params:{content:[{type:"text",text:JSON.stringify(env)}]}});}
    else if(m.method==="ui/notifications/size-changed"){
      f.style.height=m.params.height+"px";seen[id]++;
      document.body.setAttribute("data-"+id, seen[id]);}});
  f.srcdoc=window.__CARD__;
}
// The OLDER card mounts first, exactly as a conversation replays it.
wire("a", window.__A__);
setTimeout(()=>wire("b", window.__B__), 400);
</script></body></html>`;
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(page);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  const tab = await browser.newPage({ viewport: { width: 800, height: 1400 } });
  await tab.goto(`http://127.0.0.1:${port}/`);
  await tab.waitForFunction(() => document.body.hasAttribute("data-b"), {
    timeout: 20000,
  });
  await tab.waitForTimeout(900);
  const frames = tab.frames().filter((fr) => fr !== tab.mainFrame());
  const out = {
    origin: await tab.evaluate(() => location.origin),
    channelAvailable: await frames[0].evaluate(
      () => typeof BroadcastChannel === "function",
    ),
    frameOrigins: await Promise.all(
      frames.map((fr) => fr.evaluate(() => location.origin)),
    ),
    a: await frames[0].evaluate(PROBE),
    b: await frames[1].evaluate(PROBE),
  };
  await tab.close();
  server.close();
  return out;
}

const out = {};

// ── B.1 · the backtest receipt ───────────────────────────────────────
const completed = readFixture("c_bt_receipt_completed.envelope.json");
out.receipt = await measure("backtest", completed, {
  click: ".rr .rr-act[aria-expanded]",
  shot: "receipt-720-dark",
});
out.receiptLight = await measure("backtest", completed, {
  theme: "light",
  shot: "receipt-720-light",
});
out.receiptNarrow = await measure("backtest", completed, {
  width: 390,
  shot: "receipt-390-dark",
});
out.receiptNarrowLight = await measure("backtest", completed, {
  width: 390,
  theme: "light",
  shot: "receipt-390-light",
});
out.receiptOpenShot = await measure("backtest", completed, {
  click: ".rr .rr-act[aria-expanded]",
  shot: "receipt-open-720-light",
  shotAfter: true,
  theme: "light",
});
out.receiptOpenShotDark = await measure("backtest", completed, {
  click: ".rr .rr-act[aria-expanded]",
  shot: "receipt-open-720-dark",
  shotAfter: true,
});
out.receiptOpenNarrow = await measure("backtest", completed, {
  width: 390,
  click: ".rr .rr-act[aria-expanded]",
  shot: "receipt-open-390-dark",
  shotAfter: true,
});
// Q-1880: a completed receipt of a deliberate sub-window names its window;
// the same receipt over the default window does not.
const withWindow = (served, completedAt) => {
  const env = structuredClone(completed);
  env.window = served;
  env.completed_at = completedAt;
  // `view.window` is the served window's date pair (`window_block`).
  env.view.window = {
    start: served.ran.start,
    end: served.ran.end_exclusive,
    last_bar: served.ran.last_bar,
    days: served.ran.days,
  };
  return env;
};
out.receiptSubWindow = await measure(
  "backtest",
  withWindow(
    {
      requested: { start: "2025-08-25", end: "2026-09-23" },
      ran: {
        start: "2025-08-25",
        end_exclusive: "2026-09-23",
        last_bar: "2026-09-22",
        days: 394,
      },
    },
    "2026-09-23T10:00:00Z",
  ),
  {},
);
out.receiptFirstHalf = await measure(
  "backtest",
  withWindow(
    {
      requested: { start: null, end: "2025-08-25" },
      ran: {
        start: "2024-07-27",
        end_exclusive: "2025-08-25",
        last_bar: "2025-08-24",
        days: 394,
      },
    },
    "2026-09-23T10:00:00Z",
  ),
  {},
);
out.receiptFullWindow = await measure(
  "backtest",
  withWindow(
    {
      requested: { start: null, end: "2026-09-23" },
      ran: {
        start: "2024-07-27",
        end_exclusive: "2026-09-23",
        last_bar: "2026-09-22",
        days: 788,
      },
    },
    "2026-09-23T10:00:00Z",
  ),
  {},
);
out.receiptQueued = await measure(
  "backtest",
  readFixture("c_bt_receipt_queued.envelope.json"),
  { shot: "receipt-queued-720-dark" },
);
out.receiptRunning = await measure(
  "backtest",
  readFixture("c_bt_receipt_running.envelope.json"),
  {},
);
out.receiptFailed = await measure(
  "backtest",
  readFixture("c_bt_receipt_failed.envelope.json"),
  { click: ".rr .rr-act[aria-expanded]", shot: "receipt-failed-720-dark" },
);
out.receiptFailedOpenShot = await measure(
  "backtest",
  readFixture("c_bt_receipt_failed.envelope.json"),
  {
    click: ".rr .rr-act[aria-expanded]",
    shot: "receipt-failed-open-720-dark",
    shotAfter: true,
  },
);
out.receiptCancelled = await measure(
  "backtest",
  readFixture("c_bt_receipt_cancelled.envelope.json"),
  {},
);
out.receiptError = await measure(
  "backtest",
  readFixture("c_bt_error_quota.envelope.json"),
  { shot: "receipt-error-720-dark" },
);
// Q-1713: the §13.5 envelope and the never-submitted view are two
// different statements, and only one of them is about a submission.
// This is staging's own `keel_backtest_summarize` refusal on an id that
// does not exist (`c8b_error_envelope.json`) — a LOOKUP, where nothing
// was ever being submitted.
out.lookupError = await measure(
  "backtest",
  {
    code: "not_found",
    message: "Backtest btr_ZZZZZZZZZZZZZZZZZZZZZZZZZZ not found.",
  },
  { shot: "lookup-error-720-dark" },
);
// …and the arm that KEEPS the §4.1 lead: a view carrying an error and
// no status, which is a run that genuinely never started. Built from
// the completed receipt so the two arms differ in exactly one thing.
const neverSubmitted = JSON.parse(JSON.stringify(completed));
delete neverSubmitted.view.status;
neverSubmitted.view.error = "The universe resolved to no assets.";
out.neverSubmitted = await measure("backtest", neverSubmitted, {});
out.neverSubmitted.expected = {
  lead: "Backtest not submitted — ",
  error: neverSubmitted.view.error,
  lookupMessage: "Backtest not found.",
  quotaMessage: readFixture("c_bt_error_quota.envelope.json").message,
};
// Q-1719: the window shape Q-1709 newly made reachable and no fixture
// carried. `window_block` now answers `{start: null, end: null}` for a
// value that is not a date, rather than passing the wire's string
// through — so a `null` member is a thing the card WILL be handed, and
// it must draw no stamp and no "null" rather than a broken range.
const windowless = JSON.parse(JSON.stringify(completed));
windowless.view.status = "queued";
delete windowless.view.metrics;
windowless.view.window = { start: null, end: null };
out.receiptWindowless = await measure("backtest", windowless, {});

// Evidence size: the same card, the size summarize attaches.
const evidence = JSON.parse(JSON.stringify(completed));
evidence.view.size = "evidence";
out.evidence = await measure("backtest", evidence, {});

// ── B.2 · the strategy receipt ───────────────────────────────────────
const strategyReceipt = readFixture("strategy_receipt.envelope.json");
out.strategyReceipt = await measure("strategy", strategyReceipt, {
  click: ".rr .rr-act[aria-expanded]",
  shot: "strategy-receipt-720-dark",
});
out.strategyReceiptOpenShot = await measure("strategy", strategyReceipt, {
  click: ".rr .rr-act[aria-expanded]",
  shot: "strategy-receipt-open-720-dark",
  shotAfter: true,
});
out.strategyReceiptNarrow = await measure("strategy", strategyReceipt, {
  width: 390,
  shot: "strategy-receipt-390-dark",
});
out.strategyReceiptLight = await measure("strategy", strategyReceipt, {
  theme: "light",
  shot: "strategy-receipt-720-light",
});
// ── Q-1848 · a dry run is the agent's draft check ────────────────────
// One muted row, no disclosure, no action band, in both dialects — the
// parser's message and the source stay with the model.
const preview = readFixture("strategy_preview.envelope.json");
out.preview = await measure("strategy", preview, {
  shot: "strategy-draft-check-720-dark",
});
out.previewNarrow = await measure("strategy", preview, {
  width: 390,
  theme: "light",
  shot: "strategy-draft-check-390-light",
});
const parseError = readFixture("strategy_parse_error.envelope.json");
out.draftParseError = await measure("strategy", parseError, {
  shot: "strategy-draft-parse-error-720-dark",
});
out.draftParseErrorOpenAI = await measure("strategy", parseError, {
  openai: true,
  shot: "strategy-draft-parse-error-openai-720-dark",
});
// A SAVED receipt with no name keeps Q-1630's word "Untitled". (Q-1717's
// preview arm retired at the 2026-09-23 integration: a dry run renders
// the Q-1848 draft-check row, and the server now names it from the source.)
const receiptUnnamed = JSON.parse(JSON.stringify(strategyReceipt));
receiptUnnamed.view.name = null;
out.receiptUnnamed = await measure("strategy", receiptUnnamed, {});

// ── Q-1700 · a declaration-only change prints its summary ONCE ───────
// ── Q-1707 · and draws the declarations it changed ───────────────────
out.declarationChange = await measure(
  "strategy",
  readFixture("strategy_declaration_change.envelope.json"),
  { shot: "declaration-change-720-dark" },
);
// One declaration key ⇒ the same receipt row a single param edit gets:
// `Execution · buffer  0.1 → 0.05` (the approved design, review §12).
out.declarationReceipt = await measure(
  "strategy",
  readFixture("strategy_declaration_receipt.envelope.json"),
  { shot: "declaration-receipt-720-dark" },
);

// ── B.3 · one action row on every kind ───────────────────────────────
out.actionRows = {};
for (const [kind, fixture] of [
  ["backtest", "c_backtest.envelope.json"],
  ["strategy", "strategy_hrp.envelope.json"],
  ["live", "c_live_overview.envelope.json"],
  ["preflight", "c_preflight.envelope.json"],
]) {
  const env = readFixture(fixture);
  if (kind === "preflight")
    env.confirmation_expires_at = new Date(Date.now() + 840000).toISOString();
  out.actionRows[kind] = await measure(kind, env, {});
}

// ── B.4 · the comparison card ────────────────────────────────────────
const cmp2 = readFixture("compare_2.envelope.json");
const cmp4 = readFixture("compare_4.envelope.json");
const cmp8 = readFixture("compare_8.envelope.json");
const cross = readFixture("compare_cross.envelope.json");
// The same four runs as a CURRENT server serves them: `view.rows` is the
// server's row list (`build_comparison_view` -> `_CARD_ROWS`, Q-1787), so
// the count row draws under its SERVED label (Q-1906) - the card's static
// HTML carries no label for it. test_widgets_cadence pins this list to
// `_CARD_ROWS`, so it cannot drift from the server.
const SERVED_ROWS = [
  { key: "total_return_pct", label: "Return" },
  { key: "max_drawdown_pct", label: "Max drawdown", short_label: "Max DD" },
  { key: "sharpe", label: "Sharpe" },
  { key: "win_rate_pct", label: "Win rate" },
  { key: "round_trips", label: "Trades" },
  { key: "turnover", label: "Turnover (× capital)", short_label: "Turnover" },
  { key: "sortino", label: "Sortino" },
  { key: "calmar", label: "Calmar" },
  { key: "profit_factor", label: "Profit factor" },
  { key: "fees_paid", label: "Fees paid" },
];
const cmp4Served = JSON.parse(JSON.stringify(cmp4));
cmp4Served.view.rows = SERVED_ROWS;
out.compare2 = await measure("compare", cmp2, { shot: "compare-2-720-dark" });
out.compare2Light = await measure("compare", cmp2, {
  theme: "light",
  shot: "compare-2-720-light",
});
out.compare4 = await measure("compare", cmp4, {
  click: "button.more",
  shot: "compare-4-720-dark",
});
out.compare4MoreShot = await measure("compare", cmp4, {
  click: "button.more",
  shot: "compare-4-expanded-720-dark",
  shotAfter: true,
});
out.compare4Light = await measure("compare", cmp4, {
  theme: "light",
  shot: "compare-4-720-light",
});
out.compare4Narrow = await measure("compare", cmp4, {
  width: 390,
  shot: "compare-4-390-dark",
});
out.compare4NarrowLight = await measure("compare", cmp4, {
  width: 390,
  theme: "light",
  shot: "compare-4-390-light",
});
out.compare8 = await measure("compare", cmp8, { shot: "compare-8-720-dark" });
out.compare8Light = await measure("compare", cmp8, {
  theme: "light",
  shot: "compare-8-720-light",
});
out.compare8Narrow = await measure("compare", cmp8, {
  width: 390,
  shot: "compare-8-390-dark",
});
out.compare8NarrowLight = await measure("compare", cmp8, {
  width: 390,
  theme: "light",
  shot: "compare-8-390-light",
});
out.compareFull = await measure("compare", cmp4Served, {
  displayMode: "fullscreen",
  shot: "compare-4-fullscreen-720-dark",
});
out.compare8Full = await measure("compare", cmp8, {
  displayMode: "fullscreen",
  shot: "compare-8-fullscreen-720-dark",
});
out.compareCross = await measure("compare", cross, {
  shot: "compare-cross-720-dark",
});
out.compareCrossLight = await measure("compare", cross, {
  theme: "light",
  shot: "compare-cross-720-light",
});
out.compareError = await measure(
  "compare",
  readFixture("compare_error.envelope.json"),
  { shot: "compare-error-720-dark" },
);

// ── Per-kind skeletons (BUILD §4.1) ──────────────────────────────────
// Measured BEFORE the result arrives, which is the only moment a
// skeleton exists.
out.skeletonCompare = await measure("compare", cmp4, {
  deliver: false,
  settle: 400,
  shot: "compare-skeleton-720-dark",
});
out.skeletonBacktest = await measure("backtest", completed, {
  deliver: false,
  settle: 400,
});
out.skeletonStrategy = await measure("strategy", strategyReceipt, {
  deliver: false,
  settle: 400,
});

// ── The ChatGPT wordmark switch (REVIEW §4.4) ────────────────────────
out.wordmark = {
  mcpReceipt: out.receipt.brandVisible,
  openaiReceipt: (await measure("backtest", completed, { openai: true }))
    .brandVisible,
  mcpEvidence: out.evidence.brandVisible,
  openaiEvidence: (await measure("backtest", evidence, { openai: true }))
    .brandVisible,
  mcpCompare: out.compare4.brandVisible,
  openaiCompare: (await measure("compare", cmp4, { openai: true }))
    .brandVisible,
};
out.openaiShot = await measure("compare", cmp4, {
  openai: true,
  shot: "compare-4-chatgpt-720-dark",
});

// ── B.5 · supersession, and its control arm ──────────────────────────
out.supersession = await twoFrames(
  "backtest",
  readFixture("c_bt_superseded_older.envelope.json"),
  readFixture("c_bt_superseded_younger.envelope.json"),
);
out.supersessionControl = await twoFrames(
  "backtest",
  readFixture("c_bt_superseded_older.envelope.json"),
  readFixture("c_bt_superseded_other_object.envelope.json"),
);

console.log(JSON.stringify(out, null, 1));
await browser.close();
