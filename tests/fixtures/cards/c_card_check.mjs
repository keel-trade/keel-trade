/* The card system's rendering guard (mcp-strategy-view M3/M4;
 * Q-1627 … Q-1634).
 *
 * Every rule the card system now carries is a rule about the RENDERED
 * DOM — a sign glyph in front of every coloured number, an em dash that
 * KEEPS its tile, a named remainder under every cap, no id in visible
 * text, a 36/44 px action, an honest body when no result arrives, an
 * Expand action only where the host lists fullscreen. Asserting on the
 * served JS source would prove none of it, so this drives the real card
 * through the real `ui/initialize` → `tool-result` sequence in real
 * Chromium and measures what a person would see.
 *
 * Prints ONE JSON object of measurements on stdout;
 * ``tests/test_widgets.py`` makes the assertions.
 *
 * Usage: node c_card_check.mjs --cards <dir of {kind}.html> --fixtures <dir>
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
// tests/fixtures/cards → tests → keel-sdk → keel-trade → packages → repo root
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
  console.error("usage: c_card_check.mjs --cards <dir> [--fixtures <dir>]");
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

/* Host harness. `modes` is what the HOST says it can offer; `deliver`
 * false is the "result never arrives" case; `second` is a follow-up
 * tool-result (the unlatch case). Every message the card sends is
 * recorded so the handshake itself can be asserted. */
function harness(card, envelope, opts) {
  const o = opts || {};
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope) +
    ";window.__OPTS__=" +
    JSON.stringify({
      modes: o.modes || null,
      deliver: o.deliver !== false,
      second: o.second || null,
      theme: o.theme || "dark",
      displayMode: o.displayMode || "inline",
    }) +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:var(--w)}iframe{border:0;width:100%;display:block}
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
   const ctx={theme:O.theme,displayMode:O.displayMode,styles:{variables:{}}};
   if(O.modes)ctx.availableDisplayModes=O.modes;
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:ctx}});}
 else if(m.method==="ui/notifications/initialized"){
   if(O.deliver)send(result(window.__ENV__));
   else document.body.setAttribute("data-h","0");
   if(O.second)setTimeout(()=>send(result(O.second)),120);}
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

/* Everything a card-system rule is about, read off the live DOM. */
const PROBE = () => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const txt = (el) => ((el && el.textContent) || "").trim();
  const body = document.getElementById("card-body");
  const tiles = q(".stat").map((el) => ({
    label: txt(el.querySelector(".label")),
    value: txt(el.querySelector(".value")),
    tone: el.querySelector(".value.pos")
      ? "pos"
      : el.querySelector(".value.neg")
        ? "neg"
        : "",
    missing: el.classList.contains("missing"),
  }));
  // The VALUE's own text — a narrow row wraps it in a labelled span, and
  // scanning the wrapper would read the label as the number's first glyph.
  const colouredCells = q("td.pos, td.neg, .row-item .pos, .row-item .neg").map(
    (el) => txt(el.querySelector(".v") || el),
  );
  const actions = q("#link-row .button-link").map((el) => {
    const r = el.getBoundingClientRect();
    return { text: txt(el), h: Math.round(r.height), w: Math.round(r.width) };
  });
  return {
    height: Math.round(document.documentElement.getBoundingClientRect().height),
    // Visible text = what a reviewer reads. `hidden` subtrees and the
    // skeleton are excluded by using innerText-like visibility.
    text: (document.getElementById("root") || document.body).innerText || "",
    bodyText: body ? (body.innerText || "").trim() : "",
    headName: txt(document.querySelector(".head-name")),
    headChips: q("#head-meta .chip").map(txt),
    // The window's LENGTH lives here ("2.1 years"); the receipt carries
    // the window itself. Neither may repeat the other (Q-1685).
    headWhen: txt(document.querySelector(".head-when")),
    tiles,
    colouredCells,
    moreButton: q("button.more").map(txt),
    notes: q(".note").map(txt),
    receipt: q(".receipt").map(txt),
    // S-6: the historical-data line — its words, where it sits, and the
    // ink it resolved to (compared against the theme's --muted).
    histNote: q(".hist-note").map((el) => ({
      text: txt(el),
      lastInBody: !!body && el === body.lastElementChild,
      interactive: !!el.closest("a, button, #link-row"),
      color: getComputedStyle(el).color,
      muted: getComputedStyle(document.documentElement)
        .getPropertyValue("--muted")
        .trim(),
      fontSize: getComputedStyle(el).fontSize,
    })),
    actions,
    expand: q("#link-row .button-link").some((el) => txt(el) === "Expand"),
    ths: q("table.rows th").map((el) => ({
      text: txt(el),
      scope: el.getAttribute("scope"),
    })),
    tableRows: q("table.rows tr").length - (q("table.rows th").length ? 1 : 0),
    rowItems: q(".row-item").length,
    ariaLive: q("[aria-live]").length,
    focusable: q('[tabindex="0"]').length,
    skeletons: q(".skeleton").length,
    ariaBusy: body ? body.getAttribute("aria-busy") : null,
    displayMode: document.documentElement.getAttribute("data-display-mode"),
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
    bodyBackground: getComputedStyle(document.body).backgroundColor,
    iframes: q("iframe").length,
  };
};

async function measure(kind, envelope, opts) {
  const o = opts || {};
  const width = o.width || 720;
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 1000 },
  });
  await page.setContent(harness(cardHtml(kind), envelope, o));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForFunction(() => document.body.hasAttribute("data-h"), {
    timeout: 20000,
  });
  await page.waitForTimeout(o.settle == null ? 200 : o.settle);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const m = await frame.evaluate(PROBE);
  if (o.clickExpand) {
    await frame.evaluate(() => {
      const b = Array.from(
        document.querySelectorAll("#link-row .button-link"),
      ).find((el) => (el.textContent || "").trim() === "Expand");
      if (b) b.click();
    });
    await page.waitForTimeout(250);
    Object.assign(m, { afterExpand: await frame.evaluate(PROBE) });
  }
  if (o.clickMore) {
    await frame.evaluate(() => {
      const b = document.querySelector("button.more");
      if (b) b.click();
    });
    await page.waitForTimeout(150);
    Object.assign(m, { afterMore: await frame.evaluate(PROBE) });
  }
  m.sent = await page.evaluate(() =>
    window.__SENT__.map((x) => ({ method: x.method, params: x.params })),
  );
  await page.close();
  return m;
}

/* `KeelHost.prose(input)` for each input, evaluated INSIDE a rendered
 * card frame — the shipped adapter, not a copy. Returns
 * `[{input, output}, ...]`. */
async function measureProse(inputs) {
  const page = await browser.newPage({ viewport: { width: 720, height: 600 } });
  await page.setContent(
    harness(cardHtml("backtest"), { code: "x", message: "y" }, {}),
  );
  await page.waitForFunction(() => document.body.hasAttribute("data-h"), {
    timeout: 20000,
  });
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const out = await frame.evaluate(
    (xs) => xs.map((x) => ({ input: x, output: window.KeelHost.prose(x) })),
    inputs,
  );
  await page.close();
  return out;
}

const backtest = readFixture("c_backtest.envelope.json");
const backtestSparse = readFixture("c_backtest_sparse.envelope.json");
const liveOverview = readFixture("c_live_overview.envelope.json");
const livePositions = readFixture("c_live_positions.envelope.json");
const livePortfolio = readFixture("c_live_portfolio.envelope.json");
const strategy = readFixture("strategy_hrp.envelope.json");
const compare = readFixture("compare_4.envelope.json");
const preflight = readFixture("c_preflight.envelope.json");
// The confirmation window is relative by definition — pin it 14 minutes
// out at RUN time so the card's "confirm in 14 min" is deterministic.
preflight.confirmation_expires_at = new Date(
  Date.now() + 14 * 60 * 1000,
).toISOString();

const out = {};

// ── The four kinds, inline, wide ──────────────────────────────────────
out.backtest = await measure("backtest", backtest, { clickMore: true });
out.live = await measure("live", liveOverview, { clickMore: true });
out.preflight = await measure("preflight", preflight, { clickMore: true });
out.strategy = await measure("strategy", strategy, {});
// The comparison card joins the per-kind contract set (BUILD §4.4): the
// same rules about ids, colour, remainders, transparency and tap
// targets bind it, and a kind outside this scan is a kind those rules
// do not cover.
out.compare = await measure("compare", compare, {});

// ── S-6: the historical-data line, where performance is drawn ─────────
// A phone-width comparison and a light-theme result carry it too; a
// closed one-line receipt does not (it stays one line).
out.compareNarrow = await measure("compare", compare, { width: 390 });
out.backtestLight = await measure("backtest", backtest, { theme: "light" });
out.backtestReceipt = await measure(
  "backtest",
  readFixture("c_bt_receipt_completed.envelope.json"),
  {},
);

// ── Sparse: a missing number keeps its tile; a missing name is Untitled
out.backtestSparse = await measure("backtest", backtestSparse, {});

// ── Tables: the cap names its remainder; 390 px is a list, not a grid ─
out.positionsWide = await measure("live", livePositions, {});
out.positionsNarrow = await measure("live", livePositions, { width: 390 });
out.portfolioWide = await measure("live", livePortfolio, {});

// ── Q-1627: no result ever arrives ───────────────────────────────────
out.noResult = await measure("backtest", backtest, {
  deliver: false,
  settle: 4600,
});

// ── Q-1634: a second tool result re-renders (no latch) ───────────────
out.second = await measure("backtest", backtest, {
  second: liveOverviewAsBacktest(),
  settle: 500,
});

// ── Q-1685: a window that spans years keeps BOTH years ───────────────
// Every shipped fixture is same-year, where the correct range and the
// hand-rolled one that shipped are byte-identical — which is exactly why
// the defect reached a real host. This arm is the same envelope with its
// period widened across a year boundary; only the receipt and the header
// are measured from it.
const backtestYearSpan = Object.assign({}, backtest, {
  period: { start_date: "2024-08-15", end_date: "2026-09-21" },
});
out.backtestYearSpan = await measure("backtest", backtestYearSpan, {});
// Echo the period the arm actually rendered, so the assertions read the
// expected years off the ENVELOPE rather than a literal typed twice.
out.backtestYearSpan.period = backtestYearSpan.period;

// ── Q-1712: server prose carries the server's ids ────────────────────
// The rule "no id reaches visible text" was only ever proved on prose
// the CARD composes. Staging's `not_found` refusal is a sentence the
// SERVER composed, and it embeds the run id — which the card drew
// verbatim. These arms poison every server-authored string in an
// envelope with a real-shaped ULID and assert both halves: the id is
// gone, and the sentence around it survived.
const POISON_ID = "btr_00000000000000000000000000";
const poisoned = (text) => `Backtest ${POISON_ID} ${text}`;

// The §13.5 error envelope, exactly as staging sends it (code + message,
// no view) — `c8b_error_envelope.json` in the real-envelope capture set.
out.errorProse = await measure(
  "backtest",
  { code: "not_found", message: `Backtest ${POISON_ID} not found.` },
  {},
);
// A completed run whose worker notes, card note and error text all name
// ids. `render.note` is where the card reads the full profile's
// good-result line (spec 02 §2.4 retired `nudge`) — the arm also proves
// the card draws it.
const backtestProse = Object.assign({}, backtest, {
  notes: {
    non_result: { code: "X", message: poisoned("skipped the last bar.") },
    assets: [{ code: "SYMBOL_DELISTED", message: poisoned("lost SUSHI.") }],
  },
  render: Object.assign({}, backtest.render, {
    note: poisoned("is ready to deploy."),
  }),
});
out.backtestProse = await measure("backtest", backtestProse, {});
// The redaction itself, read straight off the live `KeelHost.prose` in a
// rendered frame. The DOM probe alone cannot see the whitespace tidy —
// `innerText` collapses runs of spaces — so a tidy that stopped working
// would leave every text assertion green. These pairs are what make it
// assertable, and they exercise the empty-brackets and space-before-
// punctuation arms no envelope happens to carry.
const proseUnit = await measureProse([
  `Backtest ${POISON_ID} not found.`,
  `run ${POISON_ID} (${POISON_ID}) failed`,
  `deleted ${POISON_ID} , then stopped`,
  POISON_ID,
  "nothing to redact here",
  null,
]);

// What the arms above expect, echoed off the harness's own literals so
// the assertions cannot drift from what was actually injected. It rides
// INSIDE a measurement (the shape `backtestYearSpan.period` uses)
// because every TOP-LEVEL key of this object is read as a measurement.
out.errorProse.expected = {
  id: POISON_ID,
  unit: proseUnit,
  // The WHOLE message with the token removed — not a tail of it. The
  // `code`-read-as-words fallback ("not found") would satisfy a suffix
  // match, so the assertion pins the sentence the server wrote.
  sentences: [
    "Backtest not found.",
    "Backtest skipped the last bar.",
    "Backtest lost SUSHI.",
    "Backtest is ready to deploy.",
  ],
};

// ── Q-1632: Expand only where the host lists fullscreen ──────────────
out.inlineOnly = await measure("backtest", backtest, { modes: ["inline"] });
out.withFullscreen = await measure("backtest", backtest, {
  modes: ["inline", "fullscreen"],
  clickExpand: true,
});

function liveOverviewAsBacktest() {
  // The same card kind, a different result: what a host sends when the
  // user re-runs. Only the name has to differ for the assertion.
  return Object.assign({}, backtest, {
    strategy_name: "second_result_strategy",
    sequence_number: 3,
  });
}

console.log(JSON.stringify(out, null, 1));
await browser.close();
