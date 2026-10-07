/* The receipt-row fit guard (Q-1771 / Q-1772).
 *
 * The one-row receipt is laid out by the browser, so whether its actions
 * are clipped is a LAYOUT fact: this renders each receipt fixture through
 * the real `ui/initialize` → `tool-result` sequence in real Chromium at
 * the widths the real-host sweep measured (360 … 720 px) and reads the
 * geometry of every part of the row.
 *
 * Prints ONE JSON object on stdout; ``tests/test_widgets_receipt_fit.py``
 * makes the assertions.
 *
 * Usage: node c_receipt_fit_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>]
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
    "usage: c_receipt_fit_check.mjs --cards <dir> [--fixtures <dir>]",
  );
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
     theme:"dark",displayMode:"inline",availableDisplayModes:["inline"],styles:{variables:{}}}}});}
 else if(m.method==="ui/notifications/initialized"){
   send({jsonrpc:"2.0",method:"ui/notifications/tool-result",
     params:{content:[{type:"text",text:JSON.stringify(window.__ENV__)}]}});}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";document.body.setAttribute("data-h","1");}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const PROBE = () => {
  const box = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return null;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return {
      l: r.left,
      r: r.right,
      t: r.top,
      b: r.bottom,
      w: el.clientWidth,
      sw: el.scrollWidth,
    };
  };
  const txt = (el) => ((el && el.textContent) || "").trim();
  const root = document.getElementById("root");
  const rcs = getComputedStyle(root);
  const inner = {
    l: root.getBoundingClientRect().left + parseFloat(rcs.paddingLeft),
    r: root.getBoundingClientRect().right - parseFloat(rcs.paddingRight),
  };
  const row = document.querySelector(".rr-wrap .rr");
  if (!row) return { row: null };
  const items = Array.from(row.querySelectorAll("*")).filter(
    (el) =>
      el.children.length === 0 ||
      el.classList.contains("rr-nums") ||
      el.classList.contains("rr-sc"),
  );
  const visible = (el) => box(el) !== null;
  // Top-level parts of the row in DOM order, flattened through any
  // grouping wrappers (a two-line layout groups them; the ORDER is what
  // a reader sees and is what the chip-order rule is about).
  const parts = Array.from(
    row.querySelectorAll(
      ".brand, .rr-name, .chip, .rr-nums, .rr-win, .rr-err, .rr-sc, .rr-act",
    ),
  )
    .filter(visible)
    .map((el) => ({
      cls: el.className,
      // A middle-truncated name (Q-1780) is compared by what it IS.
      text: el.getAttribute("data-full") || txt(el),
      shown: txt(el),
      box: box(el),
      // Which line group the part belongs to (1 = who it is, 2 = what it
      // says + actions); 0 when the row has no groups.
      grp: el.closest(".rr-l1") ? 1 : el.closest(".rr-l2") ? 2 : 0,
    }));
  const acts = Array.from(row.querySelectorAll(".rr-act")).map((el) => ({
    text: txt(el),
    box: box(el),
  }));
  const name = row.querySelector(".rr-name");
  const tops = Array.from(new Set(parts.map((p) => Math.round(p.box.t))));
  return {
    inner,
    row: box(row),
    parts,
    acts,
    name: box(name),
    nameText: txt(name),
    nameFull: name ? name.getAttribute("data-full") || txt(name) : null,
    dropped: Array.from(row.querySelectorAll(".rr-dropped")).map(
      (el) => el.className,
    ),
    droppable: row.querySelectorAll("[data-drop]").length,
    // Provenance (Q-1797): made (in the DOM) vs on screen.
    softMade: row.querySelectorAll("[data-soft]").length,
    softShown: Array.from(row.querySelectorAll("[data-soft]")).filter(visible)
      .length,
    // The one-line name cut at all (by CSS or in the middle).
    nameCut: name
      ? name.scrollWidth > name.clientWidth + 1 ||
        (name.getAttribute("data-full") !== null &&
          name.textContent !== name.getAttribute("data-full"))
      : false,
    // Change chips that shed their block path to fit (Q-1795).
    compact: row.querySelectorAll(".rr-compact").length,
    oneLine: name
      ? getComputedStyle(name.parentNode).display === "contents"
      : false,
    // Every chip and note the row MADE, shown or dropped — what it says.
    said: Array.from(row.querySelectorAll(".chip, .rr-win")).map(txt),
    lines: tops.length,
    height: Math.round(root.getBoundingClientRect().height),
    overflowX:
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
    items: items.length,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(kind, envelope, width, supersedeBy) {
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 900 },
  });
  await page.setContent(harness(cardHtml(kind), envelope));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForFunction(() => document.body.hasAttribute("data-h"), {
    timeout: 20000,
  });
  await page.waitForTimeout(250);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  if (supersedeBy) {
    // A younger sibling for the same object, announced the way a real
    // sibling frame announces itself (BUILD §2.9).
    await frame.evaluate((key) => {
      new BroadcastChannel("keel-cards").postMessage(key);
    }, supersedeBy);
    await page.waitForTimeout(250);
  }
  const m = await frame.evaluate(PROBE);
  m.superseded = !!supersedeBy;
  await page.close();
  m.width = width;
  return m;
}

/* A real-length name: what a user actually calls a strategy. */
const LONG = "Simple Momentum (Top 30 by volume, weekly rebalance)";
function renamed(env) {
  const e = JSON.parse(JSON.stringify(env));
  e.view.name = LONG;
  if (e.strategy_name) e.strategy_name = LONG;
  return e;
}

const FIXTURES = {
  btCompleted: ["backtest", "c_bt_receipt_completed.envelope.json"],
  btFailed: ["backtest", "c_bt_receipt_failed.envelope.json"],
  btRunning: ["backtest", "c_bt_receipt_running.envelope.json"],
  stSave: ["strategy", "strategy_receipt.envelope.json"],
  stDecl: ["strategy", "strategy_declaration_receipt.envelope.json"],
  stPreview: ["strategy", "strategy_preview.envelope.json"],
};
const WIDTHS = [360, 430, 479, 481, 560, 640, 720];

const out = { widths: WIDTHS, long: LONG, arms: {} };
const jobs = [];
for (const [name, [kind, file]] of Object.entries(FIXTURES)) {
  const env = readFixture(file);
  for (const w of WIDTHS) {
    jobs.push(
      measure(kind, env, w).then((m) => {
        out.arms[`${name}@${w}`] = m;
      }),
    );
    jobs.push(
      measure(kind, renamed(env), w).then((m) => {
        out.arms[`${name}Long@${w}`] = m;
      }),
    );
  }
}
/* Superseded arms: the chip-order rule (name · version · state ·
 * superseded · change) is only observable with a superseded chip on
 * screen. The younger key names a LATER version of the same object. */
const SUPERSEDED = {
  stPreviewSup: ["strategy", "strategy_preview.envelope.json"],
  stDeclSup: ["strategy", "strategy_declaration_receipt.envelope.json"],
  btCompletedSup: ["backtest", "c_bt_receipt_completed.envelope.json"],
};
for (const [name, [kind, file]] of Object.entries(SUPERSEDED)) {
  const env = readFixture(file);
  const key = {
    object: env.view.object,
    at: "2099-01-01T00:00:00.000Z",
    seq: 999,
    version: (env.view.version || 4) + 1,
    instanceId: "younger-sibling",
  };
  for (const w of [430, 720]) {
    jobs.push(
      measure(kind, env, w, key).then((m) => {
        out.arms[`${name}@${w}`] = m;
      }),
    );
  }
}
/* Copy arms (Q-1775). A read of a strategy has no change to show, and
 * saves nothing; and a younger sibling showing the SAME version (a run's
 * receipt followed by its own summary) is not a newer thing. */
{
  const get = readFixture("strategy_receipt.envelope.json");
  delete get.view.change;
  delete get.view.previous_version;
  jobs.push(
    measure("strategy", get, 720).then((m) => {
      m.statusText = get.view.status_text;
      out.arms["copyStGet@720"] = m;
    }),
  );
  const run = readFixture("c_bt_receipt_completed.envelope.json");
  jobs.push(
    measure("backtest", run, 720, {
      object: run.view.object,
      at: "2099-01-01T00:00:00.000Z",
      seq: 999,
      version: run.view.version,
      instanceId: "same-run-summary",
    }).then((m) => {
      out.arms["copyBtSame@720"] = m;
    }),
  );
}
/* The founder's re-test (claude.ai, 2026-09-22): "Simple Mean Reversion
 * (Top 20 Perps)" saved as v1 → v2 → v3, each version a lookback edit on
 * TimeSeriesMeanReversionForecast. `mrRead` is a read of it (no change,
 * so the row carries the provenance line); `mrReadSup` is v1 once v3
 * superseded it — the row the founder saw squeeze the name (Q-1797). */
const MR_NAME = "Simple Mean Reversion (Top 20 Perps)";
out.mrName = MR_NAME;
function mrRead() {
  const e = readFixture("strategy_receipt.envelope.json");
  e.view.name = MR_NAME;
  delete e.view.change;
  delete e.view.previous_version;
  e.view.status_text = "Draft · updated Sep 23";
  return e;
}
const MR_WIDTHS = [481, 560, 640, 720, 1000];
for (const w of MR_WIDTHS) {
  jobs.push(
    measure("strategy", mrRead(), w).then((m) => {
      out.arms[`mrRead@${w}`] = m;
    }),
  );
}
{
  const env = mrRead();
  const younger = {
    object: env.view.object,
    at: "2099-01-01T00:00:00.000Z",
    seq: 999,
    version: env.view.version + 2,
    instanceId: "v3-save",
  };
  for (const w of [720, 1000]) {
    jobs.push(
      measure("strategy", env, w, younger).then((m) => {
        out.arms[`mrReadSup@${w}`] = m;
      }),
    );
  }
}
/* `mrSave` is v2 of the same strategy: the ONE change is a block
 * parameter on a long component name — the receipt the founder saw
 * read only "v2 valid" (Q-1795). */
function mrSave() {
  const e = readFixture("strategy_receipt.envelope.json");
  e.view.name = MR_NAME;
  const b = e.view.structure.blocks[1];
  b.component = "TimeSeriesMeanReversionForecast";
  b.params = { k: 10 };
  e.view.categories.TimeSeriesMeanReversionForecast = "indicator";
  e.view.change.changed = [
    {
      id: b.id,
      component: b.component,
      path: [],
      params: { k: { old: 20, new: 10 } },
    },
  ];
  e.view.change.summary_text = "1 changed";
  return e;
}
for (const w of MR_WIDTHS) {
  jobs.push(
    measure("strategy", mrSave(), w).then((m) => {
      out.arms[`mrSave@${w}`] = m;
    }),
  );
}
/* `mrSaveSup` is that v2 receipt once v3 was saved: it must say BOTH what
 * it changed and that it was superseded (Q-1796). */
{
  const env = mrSave();
  const younger = {
    object: env.view.object,
    at: "2099-01-01T00:00:00.000Z",
    seq: 999,
    version: env.view.version + 1,
    instanceId: "v3-save",
  };
  for (const w of [640, 720, 1000]) {
    jobs.push(
      measure("strategy", env, w, younger).then((m) => {
        out.arms[`mrSaveSup@${w}`] = m;
      }),
    );
  }
}
await Promise.all(jobs);
console.log(JSON.stringify(out));
await browser.close();
