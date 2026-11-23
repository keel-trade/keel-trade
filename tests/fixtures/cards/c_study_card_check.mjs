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
    // CARD-RENDER #3: the state, date and maker on the muted second line;
    // the note chips there too (never dropped at 360 px).
    headSub: t(document.querySelector("#card-head .st-sub-w")),
    noteChips: q("#card-head .st-sub .chip").filter(visible).map(t),
    steps: q(".st-run-box .st-step").map(t),
    runChart: !!document.querySelector(".st-run-box .st-chart"),
    runWords: t(document.querySelector(".st-run-words")),
    listEmpty: t(document.querySelector(".st-list-empty")),
    issues: q(".st-issue").map(t),
    kindChip: t(document.querySelector("#head-meta .chip.kind")),
    lead: t(document.querySelector(".st-lead")),
    tiles: q(".st-number").map((e) => ({
      value: t(e.querySelector(".st-value")),
      // D-380: a number served as a sentence draws its words on their own
      // line under the number (874ac3f979); the served string is the two.
      valueWords: t(e.querySelector(".st-value-words")),
      sample: t(e.querySelector(".st-sample")),
      label: t(e.querySelector(".st-label")),
      title: e.title || "",
    })),
    // CARD-360: words a person sees cut. A visible element whose text
    // overflows its box (either axis) is cut by the CSS ellipsis or clip.
    cut: (() => {
      const over = (e) =>
        visible(e) &&
        (e.scrollWidth > e.clientWidth + 1 ||
          e.scrollHeight > e.clientHeight + 1);
      const cells = q(".st-key-slot .st-cellwords span").filter(
        (e) => e.textContent,
      );
      const title = document.querySelector(".st-key-slot .st-chart-title");
      const legend = title && title.querySelector(".st-legend");
      const name = title && title.querySelector(".st-chart-name");
      const acts = document.querySelector(".rr[data-keep] .rr-acts");
      return {
        cellWords: cells.length,
        cells: cells.filter(over).map((e) => e.textContent),
        samples: q(".st-number .st-sample").filter(over).map(t),
        titleWrap: !!(title && title.hasAttribute("data-wrap")),
        titleName: !!(name && name.textContent && over(name)),
        legend: !!(legend && over(legend)),
        legendBelow: !!(
          legend &&
          name &&
          legend.getBoundingClientRect().top >=
            name.getBoundingClientRect().bottom - 1
        ),
        keepWords: q(".rr[data-keep] .rr-l2 .rr-keep").filter(over).map(t),
        keepActsLine: acts
          ? acts.parentNode.className.split(" ")[0] || null
          : null,
      };
    })(),
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
    // MCP-CARDS (D-420): the face, what a person sees of it.
    face: document.body.getAttribute("data-face") === "1",
    faceTitle: t(document.querySelector(".sf-head .sf-title")),
    faceMeta: t(document.querySelector(".sf-head .sf-meta")),
    hero: document.querySelector(".sf-hero-n")
      ? {
          value: t(document.querySelector(".sf-hero-v")),
          label: t(document.querySelector(".sf-hero-l")),
          top: Math.round(
            document.querySelector(".sf-hero-v").getBoundingClientRect().top,
          ),
          size: parseFloat(
            getComputedStyle(document.querySelector(".sf-hero-v")).fontSize,
          ),
        }
      : null,
    heroAside:
      q(".sf-aside-line").map(t).join(" ") ||
      t(document.querySelector(".sf-hero-aside")),
    asideLines: q(".sf-aside-line").map(t),
    // MCP-CARDS (founder 11-22, the failed card's dots over its words): no
    // two drawn blocks of the card overlap. Siblings are compared inside
    // each container that lays blocks side by side or stacked (the card
    // body, the run lead, the hero, the tiles, the thumbnails, the link
    // row), and every body block against every action; the count of boxes
    // compared is the probe's own non-vacuity.
    overlap: (() => {
      const box = (e) => {
        const r = e.getBoundingClientRect();
        return {
          l: r.left,
          t: r.top,
          r: r.right,
          b: r.bottom,
          w: r.width,
          h: r.height,
        };
      };
      const shown = (e) => visible(e) && e.getBoundingClientRect().height > 0.5;
      const name = (e) =>
        (e.className && typeof e.className === "string"
          ? e.className.split(" ").filter(Boolean).slice(0, 2).join(".")
          : e.tagName.toLowerCase()) || e.tagName.toLowerCase();
      const hit = (a, b) =>
        Math.min(a.r, b.r) - Math.max(a.l, b.l) > 1 &&
        Math.min(a.b, b.b) - Math.max(a.t, b.t) > 1;
      const pairs = [];
      let compared = 0;
      const within = (els) => {
        const list = els.filter(shown);
        compared += list.length;
        for (let i = 0; i < list.length; i++)
          for (let j = i + 1; j < list.length; j++)
            if (hit(box(list[i]), box(list[j])))
              pairs.push(name(list[i]) + " × " + name(list[j]));
      };
      const kids = (sel) => q(sel).flatMap((c) => Array.from(c.children));
      const body = document.getElementById("card-body");
      const actions = q("#link-row > *").filter(shown);
      if (body) within(Array.from(body.children).concat(actions));
      within(kids(".sf-run-lead"));
      within(kids(".sf-hero"));
      within(kids(".sf-hero-aside"));
      within(kids(".sf-tiles"));
      within(kids(".sf-thumb-row"));
      return { pairs, compared };
    })(),
    pips: q(".sf-pips .sf-pip").map((e) => e.getAttribute("data-state")),
    pipTitles: q(".sf-pips .sf-pip").map((e) => e.title || ""),
    dots: q(".sf-dot").map((e) => e.getAttribute("data-state")),
    faceTiles: q(".sf-tile").map((e) => ({
      value: t(e.querySelector(".sf-tile-v")),
      label: t(e.querySelector(".sf-tile-l")),
      sub: t(e.querySelector(".sf-tile-s")),
      state: e.getAttribute("data-state"),
      pip: (
        e.querySelector(".sf-tile-pip") || { getAttribute: () => null }
      ).getAttribute("data-state"),
      title: e.title || "",
      // the value drawn whole, or cut by its box; the label may ellipsize (its title is whole)
      cut: (() => {
        const v = e.querySelector(".sf-tile-v");
        return !!(v && visible(v) && v.scrollWidth > v.clientWidth + 1);
      })(),
    })),
    faceChart: !!document.querySelector(".sf-chart .st-chart"),
    faceReadout: t(document.querySelector(".sf-chart .st-readout")),
    faceSteps: q(".sf-step, .sf-plan-step").map((e) => ({
      label: t(e.querySelector(".sf-step-l")),
      words: t(e.querySelector(".sf-step-w")),
      state: e.getAttribute("data-state"),
    })),
    faceRows: q(".sf-row").map((e) => ({
      title: t(e.querySelector(".sf-row-title")),
      meta: t(e.querySelector(".sf-row-meta")),
      value: t(e.querySelector(".sf-row-v")),
      href: (
        e.querySelector("a.sf-row-link") || { getAttribute: () => null }
      ).getAttribute("href"),
      pic: !!e.querySelector(".sf-row-pic svg, .sf-row-pic .st-picture"),
    })),
    captions: q(".sf-caption").map(t),
    // D-420d: the thumbnails under the key chart, each with its visible marks
    thumbs: q(".sf-thumb").map((b) => {
      const pic = b.querySelector(".sf-thumb-pic");
      const r = pic ? pic.getBoundingClientRect() : { width: 0, height: 0 };
      return {
        index: b.getAttribute("data-index"),
        title: b.title || "",
        caption: t(b.querySelector(".sf-thumb-t")),
        marks: Array.from(
          b.querySelectorAll(".st-line, .st-bar, .st-bin, .st-dot, .st-cell"),
        ).filter((m) => {
          // a dot is a zero-length round-capped stroke: no box, still ink
          const cs = getComputedStyle(m);
          const mr = m.getBoundingClientRect();
          return (
            cs.display !== "none" &&
            cs.visibility !== "hidden" &&
            (mr.width > 0.5 ||
              mr.height > 0.5 ||
              (m.classList.contains("st-dot") &&
                parseFloat(cs.strokeWidth) > 0))
          );
        }).length,
        w: Math.round(r.width),
        h: Math.round(r.height),
        top: Math.round(b.getBoundingClientRect().top),
        captionLines: (() => {
          const c = b.querySelector(".sf-thumb-t");
          if (!c) return 0;
          return Math.round(
            c.getBoundingClientRect().height /
              parseFloat(getComputedStyle(c).lineHeight),
          );
        })(),
      };
    }),
    more: t(document.querySelector(".sf-more")),
    // the bottom of the last drawn thing inside the card vs the card's own bottom: a blank band
    blank: (() => {
      const card = document.querySelector(".keel-card");
      if (!card) return null;
      const kids = Array.from(
        card.querySelectorAll("#card-body > *, #link-row > *"),
      ).filter(visible);
      if (!kids.length) return null;
      const last = Math.max(
        ...kids.map((k) => k.getBoundingClientRect().bottom),
      );
      const cs = getComputedStyle(card);
      return Math.round(
        card.getBoundingClientRect().bottom -
          parseFloat(cs.paddingBottom) -
          last,
      );
    })(),
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

  // Fullscreen (D-420d): the dashboard, a focused chart and back; and a
  // thumbnail tapped inline, which asks for fullscreen on its chart.
  if (full && name.startsWith("study-")) {
    const page = await browser.newPage({
      viewport: { width: 760, height: 1400 },
    });
    await page.setContent(mcpPage([res], "dark", "fullscreen"));
    await page.addStyleTag({ content: `:root{--w:760px}` });
    await page.waitForTimeout(900);
    const frame = page.frames().find((f) => f !== page.mainFrame());
    const dash = await frame.evaluate(() => {
      const q = (s) => Array.from(document.querySelectorAll(s));
      const t = (e) => ((e && e.textContent) || "").trim();
      const mk = (root) =>
        Array.from(
          root.querySelectorAll(
            ".st-line, .st-bar, .st-bin, .st-dot, .st-cell",
          ),
        ).filter((m) => {
          // a dot is a zero-length round-capped stroke: no box, still ink
          const cs = getComputedStyle(m);
          const r = m.getBoundingClientRect();
          return (
            cs.display !== "none" &&
            (r.width > 0.5 ||
              r.height > 0.5 ||
              (m.classList.contains("st-dot") &&
                parseFloat(cs.strokeWidth) > 0))
          );
        }).length;
      return {
        drawn: !!document.querySelector(".sf-dash"),
        seg: q(".sf-seg-b").map(t),
        segHidden: !!(document.querySelector(".sf-seg") || {}).hidden,
        open: (
          document.querySelector(".sf-bar .sf-open") || {
            getAttribute: () => null,
          }
        ).getAttribute("href"),
        linkRowHidden: !!(document.getElementById("link-row") || {}).hidden,
        keyTicks: document.querySelectorAll(".sf-key .st-tick").length,
        keyMarks: document.querySelector(".sf-key")
          ? mk(document.querySelector(".sf-key"))
          : 0,
        tiles: q(".sf-gtile").map((g) => ({
          index: g.getAttribute("data-index"),
          drawn: g.getAttribute("data-drawn"),
          title: t(g.querySelector(".sf-gtile-t")),
          marks: mk(g),
          text: t(g.querySelector(".sf-gtile-pic")),
          open: (
            g.querySelector(".sf-gtile-open") || { getAttribute: () => null }
          ).getAttribute("href"),
          left: Math.round(g.getBoundingClientRect().left),
        })),
        checkRows: q(".sf-crow").map((r) => ({
          text: t(r),
          pip: (
            r.querySelector(".sf-crow-pip") || { getAttribute: () => null }
          ).getAttribute("data-state"),
          chart: (
            r.querySelector(".sf-crow-chart") || { getAttribute: () => null }
          ).getAttribute("data-index"),
          chartMarks: r.querySelector(".sf-crow-chart")
            ? mk(r.querySelector(".sf-crow-chart"))
            : 0,
        })),
        numbers: q(".sf-sec#sf-numbers .st-num-row").map(t),
      };
    });
    // focus the first tile, then go back
    let focus = null;
    let back = null;
    if (dash.tiles.length) {
      await frame.click(".sf-gtile .sf-gtile-hit");
      await page.waitForTimeout(250);
      focus = await frame.evaluate(() => ({
        dashHidden: document.querySelector(".sf-dash").hidden,
        shown: !document.querySelector(".sf-focus").hidden,
        index: (
          document.querySelector(".sf-focus-panel") || {
            getAttribute: () => null,
          }
        ).getAttribute("data-index"),
        ticks: document.querySelectorAll(".sf-focus .st-tick").length,
        svg: !!document.querySelector(".sf-focus .st-plot"),
        back: (
          (document.querySelector(".sf-back") || {}).textContent || ""
        ).trim(),
        open: (
          document.querySelector(".sf-focus .sf-open-chart") || {
            getAttribute: () => null,
          }
        ).getAttribute("href"),
        height: Math.round(
          (
            document.querySelector(".sf-focus-panel") || document.body
          ).getBoundingClientRect().height,
        ),
      }));
      await frame.click(".sf-back");
      await page.waitForTimeout(200);
      back = await frame.evaluate(() => ({
        dashHidden: document.querySelector(".sf-dash").hidden,
        focusHidden: document.querySelector(".sf-focus").hidden,
      }));
    }
    await page.close();
    // a thumbnail tapped inline: fullscreen asked for, on that chart
    let tap = null;
    const ip = await browser.newPage({
      viewport: { width: 760, height: 1400 },
    });
    await ip.setContent(mcpPage([res], "dark", "inline"));
    await ip.addStyleTag({ content: `:root{--w:760px}` });
    await ip.waitForTimeout(900);
    const iframe = ip.frames().find((f) => f !== ip.mainFrame());
    const first = await iframe.evaluate(() => {
      const b = document.querySelector(".sf-thumb");
      return b ? b.getAttribute("data-index") : null;
    });
    if (first !== null) {
      await iframe.click(".sf-thumb");
      await ip.waitForTimeout(900);
      tap = {
        index: first,
        calls: await ip.evaluate(() => window.__CALLS__),
        layout: await iframe.evaluate(() =>
          document.body.getAttribute("data-layout"),
        ),
        focus: await iframe.evaluate(() => {
          const f = document.querySelector(".sf-focus");
          const p = document.querySelector(".sf-focus-panel");
          return f && !f.hidden && p ? p.getAttribute("data-index") : null;
        }),
      };
    }
    await ip.close();
    out.full[name] = { dash, focus, back, tap };
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
