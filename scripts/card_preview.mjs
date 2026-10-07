#!/usr/bin/env node
/* Render a Keel MCP card in real Chromium behind a stub MCP Apps host and
 * screenshot it — light, dark, hover, narrow (Q-1505).
 *
 * The stub host speaks the MCP Apps wire the card expects (answers
 * `ui/initialize` with a hostContext carrying a theme + style variables,
 * delivers `ui/notifications/tool-result` after `initialized`, and sizes
 * the iframe from `ui/notifications/size-changed`), so what you see is
 * what claude.ai / ChatGPT render, minus their chrome. It also reports
 * whether the card scrolls internally (it must not) and ASSERTS that no
 * shot overflows horizontally (Q-1704). Overflow is measured two ways —
 * `scrollable` (how far the frame could scroll, read from body as well as
 * documentElement) and `clipped` (the widest laid-out box past the frame's
 * edge). Both are needed: the shipped cards set `overflow-x: hidden` on
 * `html` AND `body`, so the usual
 * `documentElement.scrollWidth - clientWidth` reads 0 however wide the
 * content is. More than 1px either way is a failure, printed per shot and
 * exited 1 AFTER every screenshot is taken, so the evidence survives the
 * red. Content inside an `overflow-x: auto|scroll` container is exempt.
 *
 * Usage (from the repo root; Playwright + its Chromium come from
 * services/keel-app, `cd services/keel-app && npx playwright install chromium`
 * once):
 *
 *   PYTHONPATH=libs:packages/keel-trade/keel-sdk python -c \
 *     "from keel.widgets import build_card_html; print(build_card_html('backtest'))" > /tmp/card.html
 *   node packages/keel-trade/keel-sdk/scripts/card_preview.mjs \
 *     --card /tmp/card.html --envelope path/to/envelope.json \
 *     --out ../keel-artifacts/projects/fable/agent-surface/cards \
 *     [--prefix card-live] [--width 720,390] [--no-narrow] [--allow-overflow]
 *
 * `--width` is a comma list of frame widths. The FIRST width keeps the bare
 * suffixes (`-light`, `-dark`, `-dark-hover`) so an existing invocation's
 * filenames do not move; every later width appends its own (`-light-390`).
 * Hover is shot at the first width only. The 352-wide `-narrow` shot is
 * always taken unless `--no-narrow`. `--width 720,390` is the mobile pass —
 * it exists so the mobile capture and the overflow assertion live in THIS
 * tool rather than in a second script per lane.
 *
 * `--envelope` is the tool's JSON envelope (e.g. `keel backtest summarize
 * <id> --format json` against staging). Screenshots are artifacts: keep
 * them under ../keel-artifacts (.claude/rules/artifacts.md), never in the
 * repo.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

// The ONE host context both screenshot harnesses capture under
// (Q-1718). Declaring a second one here is how this tool and the
// cadence guard came to produce PNGs that could not be compared:
// no `availableDisplayModes` meant the Expand button was missing
// from every shot this tool ever took.
import { HOST_GROUND, hostContext } from "./card_host_context.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../../..");
const require = createRequire(
  path.join(repoRoot, "services/keel-app/package.json"),
);

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

function flag(name) {
  return process.argv.includes(`--${name}`);
}

// Frame widths, in order. The first is the "bare suffix" width, so the
// pre-Q-1704 invocation (no --width) writes exactly the same four files.
function parseWidths(raw) {
  const widths = String(raw)
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean)
    .map((w) => {
      const n = Number(w);
      if (!Number.isFinite(n) || n <= 0) {
        console.error(`--width: "${w}" is not a positive number`);
        process.exit(2);
      }
      return n;
    });
  if (!widths.length) {
    console.error("--width: no widths given");
    process.exit(2);
  }
  return widths;
}

const cardPath = arg("card");
const envelopePath = arg("envelope");
const outDir = arg("out", "/tmp/keel-card-preview");
// File-name prefix so the four cards can share one output directory
// (card-backtest-dark.png, card-live-dark.png, ...).
const prefix = arg("prefix", "card");
// Q-1704: the mobile width and the overflow assertion live here, so a lane
// capturing cards does not have to write a second script to get them.
const widths = parseWidths(arg("width", "720"));
const narrowWidth = Number(arg("narrow", "352"));
const shootNarrow = !flag("no-narrow");
const allowOverflow = flag("allow-overflow");
if (!cardPath || !envelopePath) {
  console.error(
    "usage: card_preview.mjs --card <html> --envelope <json> [--out <dir>] " +
      "[--prefix <p>] [--width 720,390] [--narrow 352] [--no-narrow] " +
      "[--allow-overflow]",
  );
  process.exit(2);
}

const card = fs.readFileSync(cardPath, "utf8");
const envelope = JSON.parse(fs.readFileSync(envelopePath, "utf8"));
fs.mkdirSync(outDir, { recursive: true });

function harness(theme, frameWidth) {
  const inject =
    "window.__CARD_HTML__ = " +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    "; window.__ENVELOPE__ = " +
    JSON.stringify(envelope) +
    "; window.__HOST_CONTEXT__ = " +
    JSON.stringify(hostContext({ theme })) +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:24px;font-family:-apple-system,sans-serif;background:${theme === "dark" ? "#1f1e1c" : "#f7f7f5"}}
.frame{width:${frameWidth}px;border:0;display:block;border-radius:12px;overflow:hidden;background:${HOST_GROUND[theme]}}
iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="card"></iframe></div>
<script>${inject}
const iframe = document.getElementById("card");
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  if (m.method === "ui/initialize") {
    iframe.contentWindow.postMessage({jsonrpc:"2.0", id:m.id, result:{protocolVersion:"2026-01-26",
      hostContext:window.__HOST_CONTEXT__}}, "*");
  } else if (m.method === "ui/notifications/initialized") {
    iframe.contentWindow.postMessage({jsonrpc:"2.0", method:"ui/notifications/tool-result",
      params:{content:[{type:"text", text: JSON.stringify(window.__ENVELOPE__)}]}}, "*");
  } else if (m.method === "ui/notifications/size-changed") {
    iframe.style.height = m.params.height + "px";
    document.body.setAttribute("data-sized", m.params.height);
  }
});
iframe.srcdoc = window.__CARD_HTML__;
</script></body></html>`;
}

const { chromium } = require("playwright");
const browser = await chromium.launch();

// Every shot's horizontal-overflow measurement, collected so the run can
// take all the screenshots and THEN fail with the full list — a capture
// that dies on the first offending width hides the others.
const overflows = [];

async function shoot(theme, frameWidth, suffix, hover) {
  const page = await browser.newPage({
    viewport: { width: frameWidth + 60, height: 1200 },
    deviceScaleFactor: 2,
  });
  await page.setContent(harness(theme, frameWidth));
  await page.waitForSelector("body[data-sized]", { timeout: 8000 });
  await page.waitForTimeout(250);
  const frame = page.frames()[1];
  const scrolls = await frame.evaluate(
    () =>
      document.documentElement.scrollHeight >
      document.documentElement.clientHeight + 1,
  );
  // Horizontal overflow, measured two ways because ONE way is vacuous
  // here (Q-1704, measured 2026-09-22): the shipped cards set
  // `overflow-x: hidden` on both `html` and `body`, so
  // `documentElement.scrollWidth - clientWidth` is 0 no matter how wide
  // the content is. Probed on the real backtest card with a 1400px
  // element injected: deScroll 720 / deClient 720, body.scrollWidth 1400.
  //
  //  * `scrollable` — width the frame could scroll to, read from BOTH
  //    documentElement and body (body is the one that still grows).
  //  * `clipped`    — the widest laid-out box past the frame's edge.
  //    Content a root `overflow-x: hidden` silently CUTS OFF is a defect
  //    too, and it is invisible to every scroll-based measure.
  //
  // Anything inside a deliberate `overflow-x: auto|scroll` container is
  // exempt: that is the sanctioned way to carry a wide table.
  const probe = await frame.evaluate(() => {
    const de = document.documentElement;
    const frameW = de.clientWidth;
    const scrollable =
      Math.max(de.scrollWidth, document.body.scrollWidth) - frameW;
    const inScroller = (el) => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === "auto" || ox === "scroll") return true;
      }
      return false;
    };
    const boxes = document.querySelectorAll("body *");
    let clipped = 0;
    let widest = "";
    for (const el of boxes) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      const past = Math.round(r.right - frameW);
      if (past > clipped && !inScroller(el)) {
        clipped = past;
        widest =
          el.tagName.toLowerCase() + (el.className ? `.${el.className}` : "");
      }
    }
    return {
      scrollable: Math.max(0, Math.round(scrollable)),
      clipped: Math.max(0, clipped),
      widest,
      boxes: boxes.length,
    };
  });
  const overflowX = Math.max(probe.scrollable, probe.clipped);
  overflows.push({ suffix, frameWidth, overflowX, ...probe });
  const height = await page.getAttribute("body", "data-sized");
  const file = path.join(outDir, `${prefix}-${suffix}.png`);
  if (hover) {
    // Only the backtest card has a hover hit-rect; the others skip the
    // hover shot's mouse move rather than wait on a missing locator.
    const hit = await frame.$("svg rect.hit");
    const box = hit ? await hit.boundingBox() : null;
    if (box)
      await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.4);
    await page.waitForTimeout(100);
  }
  await page.locator(".frame").screenshot({ path: file });
  console.log(
    `${suffix}: ${frameWidth}px wide, height ${height}px, internal scroll: ` +
      `${scrolls}, overflow-x: ${overflowX}px (scrollable ${probe.scrollable}, ` +
      `clipped ${probe.clipped}), boxes: ${probe.boxes}, svg: ${
        (await frame.$("svg")) !== null
      }, iframe: ${(await frame.$("iframe")) !== null} -> ${file}`,
  );
  await page.close();
}

// The first width keeps the bare suffixes so a pre-Q-1704 invocation writes
// the same filenames; later widths carry their own. Hover is a first-width
// shot only — it exists to catch the tooltip, not to be re-measured per size.
for (const [index, width] of widths.entries()) {
  const tag = index === 0 ? "" : `-${width}`;
  await shoot("light", width, `light${tag}`, false);
  await shoot("dark", width, `dark${tag}`, false);
  if (index === 0) await shoot("dark", width, `dark-hover${tag}`, true);
}
if (shootNarrow) await shoot("light", narrowWidth, "narrow", false);
await browser.close();

// Non-vacuity before the verdict: a run that measured nothing must not
// report "no overflow". The count is the number of shots taken, which no
// card's CONTENT can move.
const expectedShots = widths.length * 2 + 1 + (shootNarrow ? 1 : 0);
if (overflows.length !== expectedShots) {
  console.error(
    `card_preview: measured ${overflows.length} shots, expected ` +
      `${expectedShots} — the overflow assertion cannot speak for this run`,
  );
  process.exit(3);
}

// Second non-vacuity arm: a shot that laid out no elements measured
// nothing, and its 0 is silence, not a pass.
const empty = overflows.filter((o) => o.boxes === 0);
if (empty.length) {
  console.error(
    "card_preview: " +
      empty.map((o) => o.suffix).join(", ") +
      " rendered zero elements — the overflow measurement is vacuous there",
  );
  process.exit(3);
}

const offenders = overflows.filter((o) => o.overflowX > 1);
if (offenders.length && !allowOverflow) {
  console.error(
    "\ncard_preview: HORIZONTAL OVERFLOW — the card is wider than its frame:",
  );
  for (const o of offenders) {
    console.error(
      `  ${o.suffix} @ ${o.frameWidth}px: ${o.overflowX}px past the frame ` +
        `(scrollable ${o.scrollable}, clipped ${o.clipped}${
          o.widest ? `, widest ${o.widest}` : ""
        })`,
    );
  }
  console.error(
    "A card must never scroll sideways in a host. Fix the card, or re-run " +
      "with --allow-overflow if you are deliberately capturing the defect.",
  );
  process.exit(1);
}
if (offenders.length) {
  console.warn(
    `card_preview: ${offenders.length} shot(s) overflow horizontally ` +
      "(--allow-overflow given, not failing)",
  );
} else {
  console.log(
    `card_preview: ${overflows.length} shot(s), zero horizontal overflow`,
  );
}
