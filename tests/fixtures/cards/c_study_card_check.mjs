/* The study card's render harness (research spec 13 §7.1, WL-CU-12).
 *
 * Drives the REAL served study card (`build_card_html("study")`) in headless
 * Chromium with every fixture in `fixtures/cards/study/` (real envelopes,
 * research.card_fixtures), under both host dialects:
 *
 *   - mcp:    an MCP Apps host: `ui/initialize` → (`tool-input`) →
 *             `ui/notifications/tool-result` carrying the fixture's content,
 *             structuredContent and _meta; it records every size-changed;
 *   - openai: a ChatGPT host: `window.openai` with toolOutput =
 *             structuredContent and toolResponseMetadata = _meta.
 *
 * at 360, 479, 481 and 760 px, light and dark, and measures what a person
 * sees. Prints ONE JSON object; tests/test_study_card_browser.py asserts.
 *
 * Usage: node c_study_card_check.mjs --card <study.html> --fixtures <dir>
 *        [--only name,name] [--widths 360,760] [--themes dark] [--full 1]
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
const card = fs.readFileSync(arg("card"), "utf8");
const dir = arg("fixtures");
const only = (arg("only", "") || "").split(",").filter(Boolean);
const widths = (arg("widths", "360,479,481,760") || "").split(",").map(Number);
const themes = (arg("themes", "light,dark") || "").split(",");
const dialects = (arg("dialects", "mcp,openai") || "").split(",");
const full = arg("full", "1") === "1";
const names = fs
  .readdirSync(dir)
  .filter((f) => f.endsWith(".json") && f !== "MANIFEST.json")
  .map((f) => f.replace(/\.json$/, ""))
  .filter((n) => !only.length || only.includes(n))
  .sort();
const fixture = (n) =>
  JSON.parse(fs.readFileSync(path.join(dir, `${n}.json`), "utf8"));

function mcpPage(results, theme, mode) {
  const frames = results
    .map(
      (_, i) =>
        `<iframe id="c${i}" style="border:0;width:var(--w);display:block;margin:0 0 8px"></iframe>`,
    )
    .join("");
  return `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0}</style></head><body>${frames}
<script>window.__CARD__=${JSON.stringify(card).replace(/<\//g, "<\\/")};
window.__RES__=${JSON.stringify(results).replace(/<\//g, "<\\/")};
window.__H__={};window.__CALLS__=[];
window.__RES__.forEach((res,i)=>{const f=document.getElementById("c"+i);window.__H__[i]=[];
 function send(m){f.contentWindow.postMessage(m,"*");}
 window.addEventListener("message",(ev)=>{if(ev.source!==f.contentWindow)return;const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
  if(m.method==="ui/initialize"){send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:${JSON.stringify(theme)},displayMode:${JSON.stringify(mode)},availableDisplayModes:["inline","fullscreen"],styles:{variables:{}}}}});}
  else if(m.method==="ui/notifications/initialized"){setTimeout(()=>send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:res}),60+i*200);}
  else if(m.method==="ui/request-display-mode"){window.__CALLS__.push(["rdm",m.params.mode]);send({jsonrpc:"2.0",id:m.id,result:{mode:m.params.mode}});}
  else if(m.method==="ui/open-link"){window.__CALLS__.push(["link",m.params.url]);send({jsonrpc:"2.0",id:m.id,result:{}});}
  else if(m.method==="ui/notifications/size-changed"){window.__H__[i].push([Date.now(),m.params.height]);f.style.height=m.params.height+"px";}});
 f.srcdoc=window.__CARD__;});
</script></body></html>`;
}

function openaiPage(res, theme) {
  const shim =
    "<script>window.__CALLS__=[];window.openai={theme:" +
    JSON.stringify(theme) +
    ",displayMode:'inline',maxHeight:2000,callTool:function(){return Promise.resolve({})}," +
    "openExternal:function(a){window.__CALLS__.push(['ext',a&&a.href])},sendFollowUpMessage:function(){}," +
    "requestDisplayMode:function(a){window.__CALLS__.push(['rdm',a&&a.mode]);return Promise.resolve(a)}," +
    "notifyIntrinsicHeight:function(h){window.__IH__=h}," +
    "toolInput:{},toolOutput:" +
    JSON.stringify(res.structuredContent).replace(/<\//g, "<\\/") +
    ",toolResponseMetadata:" +
    JSON.stringify(res._meta || {}).replace(/<\//g, "<\\/") +
    "};</script>";
  return card.replace(/<body([^>]*)>/, (_m, a) => `<body${a}>${shim}`);
}

/* What a person sees, read off the live DOM. */
const PROBE = () => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const t = (e) => ((e && e.textContent) || "").trim();
  const body = document.getElementById("card-body");
  const bodyRect = body ? body.getBoundingClientRect() : null;
  const visible = (e) => {
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return (
      cs.display !== "none" &&
      cs.visibility !== "hidden" &&
      r.width > 0 &&
      r.height > 0
    );
  };
  const line = document.querySelector(
    ".st-key-slot .st-line, .st-panel .st-line",
  );
  const points = (d) => ((d || "").match(/[ML]/g) || []).length;
  const keySvg = document.querySelector(".st-key-slot .st-plot");
  return {
    height: Math.round(document.documentElement.getBoundingClientRect().height),
    reserved:
      parseFloat(getComputedStyle(document.body).getPropertyValue("--st-h")) ||
      null,
    layout: document.body.getAttribute("data-layout"),
    stripAttr: document.body.getAttribute("data-strip"),
    noExpand: document.body.hasAttribute("data-no-expand"),
    tpl: document.body.getAttribute("data-tpl"),
    renderError: document.body.getAttribute("data-render-error"),
    narrow: window.innerWidth < 480,
    headName: t(document.querySelector(".head-name")),
    headChips: q("#head-meta .chip").filter(visible).map(t),
    kindChip: t(document.querySelector("#head-meta .chip.kind")),
    lead: t(document.querySelector(".st-lead")),
    tiles: q(".st-number").map((e) => ({
      value: t(e.querySelector(".st-value")),
      sample: t(e.querySelector(".st-sample")),
      label: t(e.querySelector(".st-label")),
    })),
    key: (
      document.querySelector(".st-key-slot") || { getAttribute: () => null }
    ).getAttribute("data-key"),
    keyLinePoints: points(line && line.getAttribute("d")),
    keyMarks: keySvg ? keySvg.querySelectorAll("path, rect, line").length : 0,
    pictureMarks: (() => {
      const p = document.querySelector(".st-key-slot .st-picture svg");
      return p ? p.querySelectorAll("path, rect, line").length : 0;
    })(),
    ticks: q(".st-key-slot .st-tick").map(t),
    axisNames: q(".st-key-slot .st-axis-name").map(t),
    heatKey: q(".st-key-slot .st-heat-key-item").map(t),
    brand: q("#card-head .brand").filter(visible).length,
    stripChecks: q(".st-strip-item .st-mini-check").length,
    terminal: document.body.getAttribute("data-terminal") === "1",
    chartName: t(document.querySelector(".st-key-slot .st-chart-name")),
    note: t(document.querySelector(".st-key-slot .st-chart-note")),
    plainText: (() => {
      const c = document.body.cloneNode(true);
      c.querySelectorAll(
        "a, button, script, style, template, [hidden]",
      ).forEach((n) => n.remove());
      return c.innerText || c.textContent || "";
    })(),
    readout: t(document.querySelector(".st-key-slot .st-readout")),
    strip: q(".st-strip-item").length,
    listRows: q(".st-list-row").length,
    links: q("#link-row .button-link")
      .filter(visible)
      .map((a) => ({ text: t(a), href: a.getAttribute("href") })),
    rrLinks: q(".rr .rr-act[href]").map((a) => a.getAttribute("href")),
    expand: q("#link-row .button-link").some((a) => t(a) === "Expand"),
    refusal: t(document.querySelector(".st-refusal-w")),
    rr: t(document.querySelector(".rr")),
    text: (document.getElementById("root") || document.body).innerText || "",
    clipped: (() => {
      if (!bodyRect || document.body.getAttribute("data-layout") === "full")
        return [];
      const out = [];
      Array.from(body.children).forEach((c) => {
        const r = c.getBoundingClientRect();
        if (r.height > 0 && r.bottom > bodyRect.bottom + 1)
          out.push(c.className + " " + Math.round(r.bottom - bodyRect.bottom));
      });
      return out;
    })(),
    overflowX:
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  };
};

const { chromium } = require("playwright");
const browser = await chromium.launch();
const out = { renders: [], hover: {}, full: {}, supersede: {}, errors: [] };

async function render(name, res, dialect, width, theme) {
  const page = await browser.newPage({ viewport: { width, height: 1400 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  let frame;
  if (dialect === "mcp") {
    await page.setContent(mcpPage([res], theme, "inline"));
    await page.addStyleTag({ content: `:root{--w:${width}px}` });
    await page.waitForFunction(() => (window.__H__[0] || []).length > 0, null, {
      timeout: 15000,
    });
    await page.waitForTimeout(500);
    frame = page.frames().find((f) => f !== page.mainFrame());
  } else {
    await page.setContent(openaiPage(res, theme));
    await page.waitForTimeout(600);
    frame = page.mainFrame();
  }
  const m = await frame.evaluate(PROBE);
  return { page, frame, m, errors };
}

for (const name of names) {
  const res = fixture(name);
  for (const dialect of dialects)
    for (const width of widths)
      for (const theme of themes) {
        // The band edges (479, 481) are measured in one theme: their question is the height.
        if (
          (width === 479 || width === 481) &&
          theme !== themes[themes.length - 1]
        )
          continue;
        const { page, frame, m, errors } = await render(
          name,
          res,
          dialect,
          width,
          theme,
        );
        const rec = { name, dialect, width, theme, ...m, errors };
        if (dialect === "mcp") {
          // No size change between first render and +3 s, and none on hover.
          const first = await page.evaluate(() => window.__H__[0].length);
          const plot = await frame.$(".st-key-slot .st-plot-box");
          if (plot && width === 760 && theme === "dark") {
            const box = await plot.boundingBox();
            const fr = await (await page.$("#c0")).boundingBox();
            const reads = [];
            for (const fx of [0.1, 0.5, 0.9]) {
              await page.mouse.move(
                fr.x + box.x + box.width * fx,
                fr.y + box.y + box.height * 0.5,
              );
              await page.waitForTimeout(60);
              reads.push(
                await frame.evaluate(() => [
                  document
                    .querySelector(".st-key-slot .st-chart")
                    .getAttribute("data-at"),
                  document.querySelector(".st-key-slot .st-readout")
                    .textContent,
                ]),
              );
            }
            await page.mouse.move(2, 2);
            await frame.focus(".st-key-slot .st-plot-box");
            await page.keyboard.press("Home");
            const k0 = await frame.evaluate(() =>
              document
                .querySelector(".st-key-slot .st-chart")
                .getAttribute("data-at"),
            );
            await page.keyboard.press("ArrowRight");
            const k1 = await frame.evaluate(() =>
              document
                .querySelector(".st-key-slot .st-chart")
                .getAttribute("data-at"),
            );
            out.hover[name] = { reads, keys: [k0, k1], served: res };
          }
          await page.waitForTimeout(
            width === 760 && theme === "dark" ? 2500 : 0,
          );
          rec.sizes = (await page.evaluate(() => window.__H__[0])).map(
            (x) => x[1],
          );
          rec.sizesAfterFirstRender = rec.sizes.slice(first - 1);
        } else {
          rec.calls = await page.evaluate(() => window.__CALLS__);
        }
        out.renders.push(rec);
        await page.close();
      }

  // Fullscreen: every tab, at one width.
  if (full && name.startsWith("study-")) {
    const page = await browser.newPage({
      viewport: { width: 760, height: 1400 },
    });
    await page.setContent(mcpPage([res], "dark", "fullscreen"));
    await page.addStyleTag({ content: `:root{--w:760px}` });
    await page.waitForTimeout(900);
    const frame = page.frames().find((f) => f !== page.mainFrame());
    const tabs = await frame.evaluate(() =>
      Array.from(document.querySelectorAll(".st-tab")).map((t) =>
        t.getAttribute("data-tab"),
      ),
    );
    const panels = [];
    for (const tab of tabs) {
      await frame.click(`.st-tab[data-tab="${tab}"]`);
      await page.waitForTimeout(60);
      panels.push(
        await frame.evaluate(() => ({
          tab: document.querySelector(".st-panel").getAttribute("data-tab"),
          axes: document.querySelectorAll(".st-panel .st-tick").length,
          svg: !!document.querySelector(".st-panel svg"),
          tableRows: document.querySelectorAll(".st-panel .st-table tr").length,
          checks: document.querySelectorAll(".st-panel .st-check").length,
          checkWords: Array.from(
            document.querySelectorAll(".st-panel .st-check"),
          ).map((c) => c.textContent),
          height: Math.round(
            document.querySelector(".st-panel").getBoundingClientRect().height,
          ),
        })),
      );
    }
    out.full[name] = { tabs, panels };
    await page.close();
  }
}

// Supersede: an older read of a study above a newer one (13 §4.4). The
// fixtures' clock is their build order: "-again" is read before "-summary".
if (
  names.includes("study-chk_v3-summary") &&
  names.includes("study-chk_v3-again")
) {
  const page = await browser.newPage({
    viewport: { width: 760, height: 1600 },
  });
  await page.setContent(
    mcpPage(
      [fixture("study-chk_v3-again"), fixture("study-chk_v3-summary")],
      "dark",
      "inline",
    ),
  );
  await page.addStyleTag({ content: `:root{--w:760px}` });
  await page.waitForTimeout(1200);
  const frames = page.frames().filter((f) => f !== page.mainFrame());
  out.supersede = {
    older: await frames[0].evaluate(() => ({
      rr: (document.querySelector(".rr") || {}).textContent || null,
    })),
    newer: await frames[1].evaluate(() => ({
      rr: (document.querySelector(".rr") || {}).textContent || null,
      layout: document.body.getAttribute("data-layout"),
    })),
  };
  await page.close();
}

await browser.close();
for (const n of names) delete (out.hover[n] || {}).served;
process.stdout.write(JSON.stringify(out));
