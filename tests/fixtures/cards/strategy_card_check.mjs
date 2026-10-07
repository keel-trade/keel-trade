/* The strategy card's rendering guard (mcp-strategy-view M2 / PLAN §4.4).
 *
 * The card's contract is about the RENDERED DOM — a block budget, a
 * collapsed parallel that names its branches, marks on exactly the changed
 * blocks, and nothing wider than the frame — so asserting on the served JS
 * source would prove nothing. This renders the real card in the real host
 * harness (the same postMessage sequence card_preview.mjs uses) and asserts
 * the contract. `tests/test_widgets_strategy.py` runs it and skips when
 * node or Playwright is absent.
 *
 * Usage: node strategy_card_check.mjs --card <html> --fixtures <dir>
 * Prints one JSON object of measurements; exits non-zero with the failed
 * assertions listed.
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

const cardPath = arg("card");
const fixtureDir = arg("fixtures", here);
if (!cardPath) {
  console.error(
    "usage: strategy_card_check.mjs --card <html> [--fixtures <dir>]",
  );
  process.exit(2);
}
const card = fs.readFileSync(cardPath, "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

// Host harness: ui/initialize → tool-result, exactly as the host drives it.
function harness(envelope, theme, wire) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__ENV__=" +
    JSON.stringify(envelope) +
    ";window.__WIRE__=" +
    JSON.stringify(wire) +
    ";window.__MD__=" +
    JSON.stringify((envelope.view && envelope.view.markdown) || "no markdown") +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:var(--w);}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
const f=document.getElementById("c");
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){f.contentWindow.postMessage({jsonrpc:"2.0",id:m.id,result:{
   protocolVersion:"2026-01-26",hostContext:{theme:"${theme}",displayMode:"inline",styles:{variables:{}}}}},"*");}
 else if(m.method==="ui/notifications/initialized"){f.contentWindow.postMessage({jsonrpc:"2.0",
   method:"ui/notifications/tool-result",params:window.__WIRE__==="structured"
     ?{content:[{type:"text",text:window.__MD__}],structuredContent:window.__ENV__}
     :window.__WIRE__==="markdown-only"
     ?{content:[{type:"text",text:window.__MD__}]}
     :{content:[{type:"text",text:JSON.stringify(window.__ENV__)}]}},"*");}
 else if(m.method==="ui/notifications/size-changed"){f.style.height=m.params.height+"px";
   document.body.setAttribute("data-h",m.params.height);}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

const { chromium } = require("playwright");
const browser = await chromium.launch();

async function measure(envelope, width, theme = "dark", wire = "json") {
  const page = await browser.newPage({
    viewport: { width: width + 48, height: 900 },
  });
  await page.setContent(harness(envelope, theme, wire));
  await page.addStyleTag({
    content: `.frame{--w:${width}px;width:${width}px}`,
  });
  await page.waitForFunction(() => document.body.hasAttribute("data-h"), {
    timeout: 15000,
  });
  await page.waitForTimeout(150);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const m = await frame.evaluate(() => {
    const q = (s) => Array.from(document.querySelectorAll(s));
    const root = document.getElementById("card-body") || document.body;
    const text = (el) => (el.textContent || "").trim();
    // A "block" is a rendered pipeline block; the collapsed parallel row is
    // one block by the budget's own accounting (PLAN §4.4).
    // The change view pre-renders the full pipeline hidden behind "Show full
    // pipeline", so every count must exclude hidden subtrees or each marked
    // block is counted twice.
    const vis = (s) => q(s).filter((el) => !el.closest(".sv-hidden"));
    const blocks = vis(".sv-blk");
    const parRows = vis(".sv-parrow");
    // VISIBILITY, not the attribute: the card sets `head.hidden = true`, but
    // card.css's `.card-head { display: flex }` beats the UA's `[hidden]`
    // rule, so the brand rendered twice while `hidden` was set. Keying this
    // on `!h.hidden` made the assertion vacuous — it could never fire on the
    // defect it exists to catch.
    const brandRows = q(".card-head").filter(
      (h) => getComputedStyle(h).display !== "none",
    );
    return {
      height: Math.round(
        document.documentElement.getBoundingClientRect().height,
      ),
      blocks: blocks.length,
      names: q(".sv-c").map(text),
      parRows: parRows.length,
      hiddenFullPipeline: q(".sv-hidden .sv-blk").length,
      parRowText: parRows.map(text),
      marks: {
        mod: vis(".sv-mod").length,
        add: vis(".sv-add").length,
        rem: vis(".sv-rem").length,
      },
      markedNames: vis(".sv-mod .sv-c, .sv-add .sv-c, .sv-rem .sv-c").map(text),
      moreRows: vis(".sv-morerow").map(text),
      branchNames: q(".sv-branch > .sv-bn, .sv-bn").map(text),
      columns: q(".sv-branches").map(
        (b) =>
          (getComputedStyle(b).gridTemplateColumns.match(/px/g) || []).length,
      ),
      // nothing may be wider than the card
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
      iframes: q("iframe").length,
      duplicateBrand: brandRows.length,
      bodyText: text(root).slice(0, 400),
    };
  });
  await page.close();
  return m;
}

// ── fixtures, and the quantities a seed cannot move (non-vacuity) ──────
const hrpEnv = readFixture("strategy_hrp.envelope.json");
const changeEnv = readFixture("strategy_change.envelope.json");
const legacyEnv = readFixture("strategy_legacy.envelope.json");

function countBlocks(list) {
  let n = 0;
  for (const b of list || []) {
    if (b.type === "parallel")
      for (const v of Object.values(b.branches || {})) n += countBlocks(v);
    else n += 1;
  }
  return n;
}
const hrpGraph = hrpEnv.view.structure;
const hrpTotal = countBlocks(hrpGraph.blocks);
const hrpBranches = Object.keys(
  (hrpGraph.blocks.find((b) => b.type === "parallel") || { branches: {} })
    .branches,
);

const fails = [];
const ok = (cond, msg) => {
  if (!cond) fails.push(msg);
};

// NON-VACUITY: the fixture really is the extreme case the rules were set on.
ok(hrpTotal === 55, `fixture drift: HRP has ${hrpTotal} blocks, expected 55`);
ok(
  hrpBranches.length === 6,
  `fixture drift: HRP's top parallel has ${hrpBranches.length} branches, expected 6`,
);

const wide = await measure(hrpEnv, 720);
// THE A↔B SEAM: since M0 the server sends view.markdown as the text block and
// the envelope as structuredContent (_mcp_adapter._promote_markdown). A card
// that only parsed content[] would render nothing in production while every
// fixture test stayed green — the text block is no longer JSON.
const structured = await measure(hrpEnv, 720, "dark", "structured");
// CONTROL for that leg: the same markdown with NO structuredContent. If the
// card still drew the pipeline, the leg above would pass whether or not the
// envelope ever arrived, and would be proving nothing.
const mdOnly = await measure(hrpEnv, 720, "dark", "markdown-only");
const narrow = await measure(hrpEnv, 390);
const change = await measure(changeEnv, 720);
const legacy = await measure(legacyEnv, 720);
const adx = await measure(readFixture("strategy_adx.envelope.json"), 720);

// 1. The budget holds: a 55-block pipeline does not render 55 blocks inline.
ok(
  wide.blocks <= 14,
  `budget: ${wide.blocks} blocks inline at 720 (cap 12 + chrome)`,
);
ok(
  wide.blocks >= 8,
  `budget: only ${wide.blocks} blocks inline — the card is not rendering`,
);
ok(
  narrow.blocks < wide.blocks,
  `budget: narrow (${narrow.blocks}) should show fewer than wide (${wide.blocks})`,
);

// 2. The parallel that does not fit is ONE row that names every branch with a count.
ok(wide.parRows >= 1, "the 45-block parallel should collapse to a counted row");
const rowText = wide.parRowText.join(" ");
for (const b of hrpBranches)
  ok(rowText.includes(b), `collapsed row omits branch "${b}"`);
ok(/\d+\s*blocks/.test(rowText), "collapsed row states no block count");

// 3. Every collapse names its remainder.
ok(
  narrow.moreRows.some((t) => /\d+\s*more/.test(t)),
  "narrow render hides blocks without a 'N more' row",
);

// 4. The change view marks exactly the changed blocks and nothing else.
ok(
  change.marks.mod === 1,
  `change: ${change.marks.mod} modified marks, expected 1`,
);
ok(
  change.marks.add === 1,
  `change: ${change.marks.add} added marks, expected 1`,
);
ok(
  change.marks.rem === 0,
  `change: ${change.marks.rem} removed marks, expected 0`,
);
ok(
  change.markedNames.includes("AboveThresholdFilter") &&
    change.markedNames.includes("VolatilityFilter"),
  `change: marked the wrong blocks (${change.markedNames.join(", ")})`,
);
ok(
  change.bodyText.includes("25") && change.bodyText.includes("30"),
  "change: old → new value missing",
);
// The hunks view must carry the full pipeline behind the swap, marked.
ok(
  change.hiddenFullPipeline >= 6,
  `change: "Show full pipeline" holds ${change.hiddenFullPipeline} blocks, expected the whole pipeline`,
);

// 5. Nothing widens the card, nothing scrolls inside it, nothing frames.
for (const [name, m] of [
  ["wide", wide],
  ["narrow", narrow],
  ["change", change],
  ["legacy", legacy],
]) {
  ok(m.overflowX <= 1, `${name}: ${m.overflowX}px of horizontal overflow`);
  ok(!m.internalScroll, `${name}: renders an internal scroll container`);
  ok(m.iframes === 0, `${name}: renders an iframe`);
  ok(
    m.duplicateBrand === 0,
    `${name}: the template brand row is still visible (duplicate "Keel")`,
  );
  ok(m.height > 120, `${name}: height ${m.height}px — the card did not render`);
}

// 5b. The production wire shape renders the same card as the fixture shape.
ok(
  structured.blocks === wide.blocks,
  `wire: structuredContent render shows ${structured.blocks} blocks, fixture shape shows ${wide.blocks}`,
);
ok(
  structured.parRows === wide.parRows,
  "wire: structuredContent render lost the collapsed parallel",
);
ok(
  structured.height > 120,
  `wire: structuredContent render is ${structured.height}px — the card did not read the envelope`,
);
ok(
  mdOnly.blocks === 0,
  `wire control: markdown alone drew ${mdOnly.blocks} blocks — the seam leg cannot distinguish a delivered envelope from a missing one`,
);

// 6. The legacy path (no `view` block) still renders the pipeline — the card
//    must be correct against a server that predates M0.
ok(
  legacy.names.includes("EqualWeightSizer"),
  "legacy envelope (no view) does not render the pipeline",
);

// 7. The REAL population extremes (O.4, measured over staging's 571 distinct
//    stored pipelines on 2026-09-20). The HRP is not the top of the
//    distribution — it sits near p95. The tail is a 189-block pipeline, an
//    8-branch parallel and a 169-asset universe, and the founder's rule is
//    that the card handles those, not the average. Each is asserted at the
//    wide AND the narrow width, because a collapse that only works at 720 is
//    not a collapse.
const xlBlocksEnv = readFixture("strategy_xl_blocks.envelope.json");
const xlBranchesEnv = readFixture("strategy_xl_branches.envelope.json");
const xlUniverseEnv = readFixture("strategy_xl_universe.envelope.json");

// Non-vacuity, read from the fixtures: a renderer seed cannot move these.
const xlBlocksTotal = countBlocks(xlBlocksEnv.view.structure.blocks);
const xlBranchMax = Math.max(
  0,
  ...xlBranchesEnv.view.structure.blocks
    .filter((b) => b.type === "parallel")
    .map((b) => Object.keys(b.branches).length),
);
const xlAssets = (xlUniverseEnv.view.structure.universe.assets || []).length;
ok(
  xlBlocksTotal >= 150,
  `fixture drift: the XL pipeline has ${xlBlocksTotal} blocks, expected the 189-block extreme`,
);
ok(
  xlBranchMax >= 8,
  `fixture drift: the XL parallel has ${xlBranchMax} branches, expected 8`,
);
ok(
  xlAssets >= 150,
  `fixture drift: the XL universe has ${xlAssets} assets, expected 169`,
);

const xlBlocksWide = await measure(xlBlocksEnv, 720);
const xlBlocksNarrow = await measure(xlBlocksEnv, 390);
const xlBranchesWide = await measure(xlBranchesEnv, 720);
const xlBranchesNarrow = await measure(xlBranchesEnv, 390);
const xlUniverseWide = await measure(xlUniverseEnv, 720);
const xlUniverseNarrow = await measure(xlUniverseEnv, 390);

for (const [name, m] of [
  ["xl_blocks@720", xlBlocksWide],
  ["xl_blocks@390", xlBlocksNarrow],
  ["xl_branches@720", xlBranchesWide],
  ["xl_branches@390", xlBranchesNarrow],
  ["xl_universe@720", xlUniverseWide],
  ["xl_universe@390", xlUniverseNarrow],
]) {
  ok(m.overflowX <= 1, `${name}: ${m.overflowX}px of horizontal overflow`);
  ok(!m.internalScroll, `${name}: renders an internal scroll container`);
  ok(m.iframes === 0, `${name}: renders an iframe`);
  ok(m.duplicateBrand === 0, `${name}: duplicate brand row`);
  ok(m.height > 120, `${name}: height ${m.height}px — the card did not render`);
  ok(
    m.blocks <= 14,
    `${name}: ${m.blocks} blocks inline — the budget did not hold`,
  );
}

// A 189-block pipeline must say how many it is not showing, at both widths.
for (const [name, m] of [
  ["xl_blocks@720", xlBlocksWide],
  ["xl_blocks@390", xlBlocksNarrow],
]) {
  ok(
    m.moreRows.some((t) => /\d+\s*more/.test(t)) || m.parRowText.length > 0,
    `${name}: hides ${xlBlocksTotal - m.blocks} blocks without saying so`,
  );
}

// An 8-branch parallel is beyond the 3-column packer, so it must collapse to a
// row that NAMES every branch — the same contract the HRP's six proved.
const xlRowText = xlBranchesWide.parRowText.join(" ");
const xlBranchNames = xlBranchesEnv.view.structure.blocks
  .filter((b) => b.type === "parallel")
  .flatMap((b) => Object.keys(b.branches));
ok(
  xlBranchesWide.parRows >= 1 || xlBranchesWide.columns.some((c) => c <= 3),
  "the 8-branch parallel neither packed into <=3 columns nor collapsed to a row",
);
if (xlBranchesWide.parRows >= 1) {
  for (const b of xlBranchNames.slice(0, 8))
    ok(xlRowText.includes(b), `8-branch collapsed row omits branch "${b}"`);
}
ok(
  xlBranchesWide.columns.every((c) => c <= 3),
  `the packer produced ${Math.max(0, ...xlBranchesWide.columns)} columns, cap is 3`,
);
ok(
  xlBranchesNarrow.columns.every((c) => c <= 1),
  `at 390 the packer produced ${Math.max(0, ...xlBranchesNarrow.columns)} columns, cap is 1`,
);

// 169 assets must not widen the card and must state the remainder.
ok(
  /\+\s*\d+/.test(xlUniverseWide.bodyText) ||
    xlUniverseWide.bodyText.includes(String(xlAssets)),
  "the 169-asset universe neither truncated with a +N nor stated its size",
);

// 8. The 3-COLUMN PACKER. Until 2026-09-20 nothing in this suite reached it:
//    the HRP's six-way parallel is large enough that the budget collapses it
//    to a row first, and every other fixture's parallel is 2-way, so the
//    `columns <= 3` assertions all ran against an EMPTY array and proved
//    nothing. This fixture is the smallest real staging pipeline carrying a
//    3-branch parallel — small enough that the packer is actually reached.
const cols3Env = readFixture("strategy_cols3.envelope.json");
const cols3Branches = Math.max(
  0,
  ...cols3Env.view.structure.blocks
    .filter((b) => b.type === "parallel")
    .map((b) => Object.keys(b.branches).length),
);
ok(
  cols3Branches >= 3,
  `fixture drift: the packer fixture has ${cols3Branches} branches, expected 3`,
);
const cols3Wide = await measure(cols3Env, 720);
const cols3Narrow = await measure(cols3Env, 390);

// The whole point: a NON-EMPTY column measurement. Without this the cap
// assertions below are the vacuity they exist to close.
ok(
  cols3Wide.columns.length > 0,
  "the packer produced no .sv-branches grid at 720 — the column rules are untested",
);
ok(
  cols3Wide.columns.some((c) => c === 3),
  `V-14: 3 branches at 720px should pack into 3 columns, got ${JSON.stringify(cols3Wide.columns)}`,
);
ok(
  cols3Narrow.columns.every((c) => c === 1),
  `V-14: under 480px every parallel is one column, got ${JSON.stringify(cols3Narrow.columns)}`,
);
ok(
  cols3Wide.overflowX <= 1,
  `cols3@720: ${cols3Wide.overflowX}px of horizontal overflow`,
);
ok(
  cols3Narrow.overflowX <= 1,
  `cols3@390: ${cols3Narrow.overflowX}px of horizontal overflow`,
);
ok(
  !cols3Wide.internalScroll,
  "cols3@720: renders an internal scroll container",
);
ok(
  cols3Wide.height > 120,
  `cols3@720: height ${cols3Wide.height}px — did not render`,
);

await browser.close();
const out = {
  hrp_total_blocks: hrpTotal,
  hrp_branches: hrpBranches.length,
  wide: {
    height: wide.height,
    blocks: wide.blocks,
    parRows: wide.parRows,
    columns: wide.columns,
  },
  narrow: {
    height: narrow.height,
    blocks: narrow.blocks,
    more: narrow.moreRows,
  },
  change: { height: change.height, marks: change.marks },
  legacy: { height: legacy.height, blocks: legacy.blocks },
  structured_wire: { height: structured.height, blocks: structured.blocks },
  wire_control_markdown_only: {
    blocks: mdOnly.blocks,
    bodyText: mdOnly.bodyText.slice(0, 90),
  },
  xl_blocks: {
    total: xlBlocksTotal,
    wide: {
      height: xlBlocksWide.height,
      blocks: xlBlocksWide.blocks,
      more: xlBlocksWide.moreRows,
    },
    narrow: { height: xlBlocksNarrow.height, blocks: xlBlocksNarrow.blocks },
  },
  xl_branches: {
    branches: xlBranchMax,
    wide: {
      height: xlBranchesWide.height,
      columns: xlBranchesWide.columns,
      parRows: xlBranchesWide.parRows,
    },
    narrow: {
      height: xlBranchesNarrow.height,
      columns: xlBranchesNarrow.columns,
    },
  },
  xl_universe: {
    assets: xlAssets,
    wide: {
      height: xlUniverseWide.height,
      overflowX: xlUniverseWide.overflowX,
    },
    narrow: {
      height: xlUniverseNarrow.height,
      overflowX: xlUniverseNarrow.overflowX,
    },
  },
  cols3: {
    branches: cols3Branches,
    wide: { height: cols3Wide.height, columns: cols3Wide.columns },
    narrow: { height: cols3Narrow.height, columns: cols3Narrow.columns },
  },
  adx: {
    height: adx.height,
    blocks: adx.blocks,
    columns: adx.columns,
    parRows: adx.parRows,
  },
  failures: fails,
};
console.log(JSON.stringify(out, null, 1));
process.exit(fails.length ? 1 : 0);
