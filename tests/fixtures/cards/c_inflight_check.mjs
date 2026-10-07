/* The in-flight guard (Q-1769).
 *
 * On claude.ai the card mounts when the tool call STARTS; a waited
 * `keel_backtest_run` returns 30 s+ later. The card used to draw a full
 * evidence skeleton, replace it after a fixed 4 s with "Result did not
 * arrive — open in Keel." while the call was still running, and then
 * collapse to a one-row receipt when the result DID arrive. Every rule
 * here is about the rendered DOM over TIME, so this drives the real
 * cards through the real `ui/initialize` → `tool-input` → (wait) →
 * `tool-result` sequence in real Chromium, and samples the card at
 * fixed instants on either side of the old 4 s mark.
 *
 * Prints ONE JSON object of samples on stdout;
 * ``tests/test_widgets_inflight.py`` makes the assertions.
 *
 * Usage: node c_inflight_check.mjs --cards <dir of {kind}.html> [--fixtures <dir>]
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
// The evidence envelope split by the SERVER's own partition (Q-1883):
// `{structured, card}` — what `structuredContent` and `_meta["keel/card"]`
// carry with KEEL_CARD_META_MOVE on. Written by the pytest fixture.
const splitPath = arg("split");
if (!cardsDir) {
  console.error("usage: c_inflight_check.mjs --cards <dir> [--fixtures <dir>]");
  process.exit(2);
}
const cardHtml = (kind) =>
  fs.readFileSync(path.join(cardsDir, `${kind}.html`), "utf8");
const readFixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(fixtureDir, n), "utf8"));

/* The host. After `initialized` it sends `tool-input` (when the arm has
 * arguments), then — on the arm's own clock — `tool-cancelled` or the
 * `tool-result`. `toolName` rides `hostContext.toolInfo`, as the spec
 * defines it, only when the arm says the host sends one. */
function harness(card, o) {
  const inject =
    "window.__CARD__=" +
    JSON.stringify(card).replace(/<\//g, "<\\/") +
    ";window.__O__=" +
    JSON.stringify(o).replace(/<\//g, "<\\/") +
    ";";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0}.frame{width:720px}iframe{border:0;width:100%;display:block}
</style></head><body><div class="frame"><iframe id="c"></iframe></div>
<script>${inject}
const O=window.__O__;
const f=document.getElementById("c");
function send(m){f.contentWindow.postMessage(m,"*");}
window.addEventListener("message",(ev)=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 if(m.method==="ui/initialize"){
   const ctx={theme:"dark",displayMode:"inline",availableDisplayModes:["inline"],styles:{variables:{}}};
   if(O.toolName)ctx.toolInfo={id:1,tool:{name:O.toolName,inputSchema:{type:"object"}}};
   send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:ctx}});}
 else if(m.method==="ui/notifications/initialized"){
   if(O.input)send({jsonrpc:"2.0",method:"ui/notifications/tool-input",params:{arguments:O.input}});
   if(O.cancelAt!=null)setTimeout(()=>send({jsonrpc:"2.0",
     method:"ui/notifications/tool-cancelled",params:{reason:"user stopped"}}),O.cancelAt);
   if(O.resultAt!=null)setTimeout(()=>send({jsonrpc:"2.0",
     method:"ui/notifications/tool-result",
     params:O.rawResult||{content:[{type:"text",text:JSON.stringify(O.result)}]}}),O.resultAt);}
 else if(m.method==="ui/notifications/size-changed"){
   f.style.height=m.params.height+"px";}});
f.srcdoc=window.__CARD__;
</script></body></html>`;
}

/* The ChatGPT dialect: `window.openai` exists before the adapter runs,
 * carrying `toolInput` and a null `toolOutput`; the output lands later
 * the way the Apps SDK delivers it — a globals update + the event. An arm
 * with `metaKey` also declares `toolResponseMetadata: null` up front, as
 * the Apps SDK declares every global (Q-1883). */
function asOpenAI(card, input, metaKey) {
  const shim =
    "<script>window.openai={theme:'dark',displayMode:'inline'," +
    "availableDisplayModes:['inline'],maxHeight:640," +
    "callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(){},sendFollowUpMessage:function(){}," +
    "requestDisplayMode:function(){return Promise.resolve({mode:'inline'})}," +
    "notifyIntrinsicHeight:function(){}," +
    "toolInput:" +
    JSON.stringify(input) +
    ",toolOutput:null" +
    (metaKey ? ",toolResponseMetadata:null" : "") +
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
  const live = document.querySelector(".sk-live");
  const head = document.getElementById("card-head");
  return {
    expect: document.body.getAttribute("data-expect"),
    dataSize: document.body.getAttribute("data-size"),
    height: Math.round(root.getBoundingClientRect().height),
    text: (root.innerText || "").trim(),
    liveVisible: vis(live),
    liveText: live ? live.innerText.trim() : null,
    headVisible: vis(head),
    skeleton: !!document.querySelector(".skeleton"),
    shapes: Array.from(document.querySelectorAll(".skeleton > *"))
      .filter(vis)
      .map((el) => el.className),
    receiptRow: vis(document.querySelector(".rr-wrap .rr")),
    chart: !!document.querySelector(".chart svg"),
    linkRow: (() => {
      const r = document.getElementById("link-row");
      return r && !r.hidden ? r.innerText.trim() : "";
    })(),
    tiles: document.querySelectorAll(".stat").length,
    // What the skeleton's tiles promise (Q-1781).
    skLabels: Array.from(
      document.querySelectorAll(".skeleton .sk-tiles i"),
    ).map((t) => t.textContent),
    ariaBusy: (document.getElementById("card-body") || {}).getAttribute
      ? document.getElementById("card-body").getAttribute("aria-busy")
      : null,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();

/* Run one arm: sample the card at each instant in `at` (ms after the
 * page loads the card). A ChatGPT arm applies its `steps` — `[at, globals]`
 * — from inside the frame, each a globals update + the event, the way the
 * Apps SDK delivers them; `resultAt`/`result` is the one-step shorthand. */
async function arm(kind, o, at) {
  const page = await browser.newPage({
    viewport: { width: 768, height: 1200 },
  });
  let card = cardHtml(kind);
  if (o.openai) card = asOpenAI(card, o.input, o.metaKey);
  // A ChatGPT arm's parent speaks the MCP Apps bridge only when the arm
  // gives it a `bridge` script (Q-1883); otherwise it answers the
  // handshake and sends nothing, as a globals-only host would.
  await page.setContent(harness(card, o.openai ? o.bridge || {} : o));
  const frame = () => page.frames().find((fr) => fr !== page.mainFrame());
  const t0 = Date.now();
  const samples = {};
  const steps = o.openai
    ? o.steps ||
      (o.resultAt != null ? [[o.resultAt, { toolOutput: o.result }]] : [])
    : [];
  let next = 0;
  for (const t of at) {
    while (next < steps.length && steps[next][0] < t) {
      const [when, globals] = steps[next++];
      await page.waitForTimeout(Math.max(0, when - (Date.now() - t0)));
      await frame().evaluate((g) => {
        Object.assign(window.openai, g);
        window.dispatchEvent(
          new CustomEvent("openai:set_globals", { detail: { globals: g } }),
        );
      }, globals);
    }
    await page.waitForTimeout(Math.max(0, t - (Date.now() - t0)));
    samples[t] = await frame().evaluate(PROBE);
  }
  await page.close();
  return samples;
}

const receipt = readFixture("c_bt_receipt_completed.envelope.json");
const evidence = readFixture("c_backtest.envelope.json");
const EARLY = 400; // in flight, well inside the old grace
const LATE = 4700; // past the old fixed 4 s timeout, still in flight
const RESULT = 5300; // the result lands after the old timeout fired
const AFTER = 5900;

/* The metadata race (Q-1883). ChatGPT fills `toolOutput` and
 * `toolResponseMetadata` as two globals; a host may fill them apart. */
const split = splitPath ? JSON.parse(fs.readFileSync(splitPath, "utf8")) : null;
const META = split
  ? {
      ui: { resourceUri: "ui://keel/cards/backtest.html" },
      "keel/card": split.card,
    }
  : null;
const OUT_AT = 800; // the structured result lands
const VIEW_INPUT = { strategy_id: "str_demo", present: "view" };
const lateMeta = (gap) =>
  arm(
    "backtest",
    {
      openai: true,
      metaKey: true,
      input: VIEW_INPUT,
      steps: [
        [OUT_AT, { toolOutput: split.structured }],
        [OUT_AT + gap, { toolResponseMetadata: META }],
      ],
    },
    [OUT_AT + 150, OUT_AT + gap - 100, OUT_AT + gap + 500],
  );
const metaRuns = split
  ? {
      // toolOutput first, its render data 300 ms / 1500 ms later.
      openaiLateMeta300: lateMeta(300),
      openaiLateMeta1500: lateMeta(1500),
      // CONTROL: the metadata never lands — the card waits out the grace,
      // then draws what `structuredContent` carried.
      openaiMetaNever: arm(
        "backtest",
        {
          openai: true,
          metaKey: true,
          input: VIEW_INPUT,
          steps: [[OUT_AT, { toolOutput: split.structured }]],
        },
        [OUT_AT + 150, OUT_AT + 2700, OUT_AT + 3600],
      ),
      // The founder's case: ChatGPT leaves `toolInput` null for the whole
      // call; the result (and its metadata) lands after the old 4 s mark.
      openaiNoInput: arm(
        "backtest",
        {
          openai: true,
          metaKey: true,
          input: null,
          steps: [
            [
              5150,
              { toolOutput: split.structured, toolResponseMetadata: META },
            ],
          ],
        },
        [400, 4700, 5900],
      ),
      // MCP Apps: ONE tool-result carries structuredContent and _meta, so
      // the ordering above cannot occur; with or without _meta the card
      // draws on the notification, never waiting.
      mcpMetaAtomic: arm(
        "backtest",
        {
          input: VIEW_INPUT,
          resultAt: OUT_AT,
          rawResult: {
            content: [{ type: "text", text: "(markdown)" }],
            structuredContent: split.structured,
            _meta: META,
          },
        },
        [OUT_AT - 300, OUT_AT + 150],
      ),
      // ChatGPT through its MCP Apps bridge: `toolInput` stays null in the
      // globals (the 2026-09-23 live observation), but the bridge sends the
      // tool's name and arguments after the handshake — the card waits as
      // the full card it will become, in words — and later the complete
      // result. A `toolOutput` update without metadata after that must not
      // strip the chart the bridge's `_meta` drew.
      openaiBridge: arm(
        "backtest",
        {
          openai: true,
          metaKey: true,
          input: null,
          bridge: {
            toolName: "keel_backtest_run",
            input: VIEW_INPUT,
            resultAt: 1500,
            rawResult: {
              content: [{ type: "text", text: "(markdown)" }],
              structuredContent: split.structured,
              _meta: META,
            },
          },
          steps: [[1600, { toolOutput: split.structured }]],
        },
        [600, 1400, 1800, 5200],
      ),
      // A compose SAVE on ChatGPT (the coordinator's ~370 px card): with the
      // arguments from the bridge the card waits as the full strategy card,
      // worded, never as the 20 px bar.
      openaiBridgeCompose: arm(
        "strategy",
        {
          openai: true,
          metaKey: true,
          input: null,
          bridge: {
            toolName: "keel_strategy_compose",
            input: { source: "Pipeline()", name: "trend_v2" },
          },
        },
        [600],
      ),
      // The metadata lands and `toolOutput` never does: the result arrived
      // with nothing for the card. After the grace it ends on the
      // empty-result line, never a call left running for 15 minutes.
      openaiOutputNever: arm(
        "strategy",
        {
          openai: true,
          metaKey: true,
          input: { strategy_id: "str_demo" },
          steps: [[OUT_AT, { toolResponseMetadata: META }]],
        },
        [OUT_AT + 150, OUT_AT + 3600],
      ),
      mcpNoMeta: arm(
        "backtest",
        {
          input: VIEW_INPUT,
          resultAt: OUT_AT,
          rawResult: {
            content: [{ type: "text", text: "(markdown)" }],
            structuredContent: split.structured,
          },
        },
        [OUT_AT + 150],
      ),
    }
  : {};

/* The results the 2026-09-23 ChatGPT sweep turn drew as blank frames — a
 * receipt-size run and a plan-limit refusal — exactly as the REAL
 * `view_tool_result` splits them with KEEL_CARD_META_MOVE on (written by
 * the pytest fixture: `{name: {kind, input, toolOutput, meta}}`). Each is
 * delivered the way a host may: the output first, its metadata 400 ms
 * later. */
const resultsPath = arg("results");
const realResults = resultsPath
  ? JSON.parse(fs.readFileSync(resultsPath, "utf8"))
  : {};
const resultRuns = {};
for (const [name, r] of Object.entries(realResults)) {
  resultRuns[name] = arm(
    r.kind,
    {
      openai: true,
      metaKey: true,
      input: r.input,
      steps: [
        [OUT_AT, { toolOutput: r.toolOutput }],
        [OUT_AT + 400, { toolResponseMetadata: r.meta }],
      ],
    },
    [OUT_AT + 150, OUT_AT + 900],
  );
}

const out = {};
const runs = {
  ...metaRuns,
  ...resultRuns,
  // (a) the founder's case: a waited backtest_run, default `present`.
  runDefault: arm(
    "backtest",
    {
      input: { strategy_id: "str_demo", wait: true },
      resultAt: RESULT - 150,
      result: receipt,
    },
    [EARLY, LATE, AFTER],
  ),
  // (a') the same with the host naming the tool (toolInfo).
  runNamed: arm(
    "backtest",
    {
      toolName: "keel_backtest_run",
      input: { strategy_id: "str_demo" },
      resultAt: RESULT - 150,
      result: receipt,
    },
    [EARLY, LATE, AFTER],
  ),
  // (b) CONTROL: no arguments and no result — the honest miss stands.
  noCall: arm("backtest", {}, [EARLY, LATE]),
  // (c) the caller asked for the full view — the full skeleton, grown once.
  presentView: arm(
    "backtest",
    {
      input: { strategy_id: "str_demo", present: "view" },
      resultAt: RESULT - 150,
      result: evidence,
    },
    [EARLY, LATE, AFTER],
  ),
  // (d) the host cancels the call: THAT is a terminal miss.
  cancelled: arm(
    "backtest",
    { input: { strategy_id: "str_demo" }, cancelAt: 800 },
    [EARLY, 1400],
  ),
  // (e) ChatGPT: toolInput present, toolOutput null, output later.
  openai: arm(
    "backtest",
    {
      openai: true,
      input: { strategy_id: "str_demo" },
      resultAt: RESULT - 150,
      result: receipt,
    },
    [EARLY, LATE, AFTER],
  ),
  // (f) strategy card: a dry run is a receipt, a read is a view.
  composeDryRun: arm(
    "strategy",
    { input: { source: "Pipeline()", dry_run: true, name: "trend_v2" } },
    [EARLY],
  ),
  strategyGet: arm("strategy", { input: { strategy_id: "str_demo" } }, [EARLY]),
  // (g) before any argument: a backtest card waits as a row, a
  // comparison waits as its table.
  preInputBacktest: arm("backtest", { holdInput: true }, [EARLY]),
  preInputCompare: arm("compare", { holdInput: true }, [EARLY]),
};
for (const [k, v] of Object.entries(runs)) out[k] = await v;
out.instants = { EARLY, LATE, RESULT, AFTER };
console.log(JSON.stringify(out, null, 1));
await browser.close();
