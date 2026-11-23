// The screen card drawn from its SAVED VIEW (Q-3635; SCREENS-CARD's face,
// D-420..D-420d): every fixture case in the input, in an MCP Apps host
// (claude.ai's shape, its documented tokens), light and dark, at a phone width
// and a wide one, inline, and (`modes` includes "fullscreen") in Expand at a
// phone and a desktop size, where it also chooses Show "All" and focuses the
// Table panel and reads the card again. Writes one screenshot per rendering
// into `input.out` and prints what the card drew, for the Python test to judge.
// Input: {html, cases: {name: envelope}, out, widths?, themes?, modes?}.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(
  path.resolve(here, "../../../../../../services/keel-app/package.json"),
);
const { chromium } = require("playwright");
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const widths = input.widths || [360, 760];
const themes = input.themes || ["light", "dark"];
const modes = input.modes || ["inline"];
const out = input.out || path.dirname(process.argv[2]);
fs.mkdirSync(out, { recursive: true });
const GROUND = { light: "#ffffff", dark: "#30302e" };
const VARS = {
  light: {
    "--color-background-secondary": "#f5f4ed",
    "--color-text-primary": "#141413",
    "--color-text-secondary": "#3d3d3a",
    "--color-border-primary": "rgba(31,30,29,0.4)",
    "--color-border-tertiary": "rgba(31,30,29,0.15)",
  },
  dark: {
    "--color-background-secondary": "#262624",
    "--color-text-primary": "#faf9f5",
    "--color-text-secondary": "#c2c0b6",
    "--color-border-primary": "rgba(222,220,209,0.4)",
    "--color-border-tertiary": "rgba(222,220,209,0.15)",
  },
};

// What the card drew, read inside its frame.
const READ = () => {
  const q = (s) => [...document.querySelectorAll(s)];
  const text = (s) => q(s).map((n) => n.textContent.trim());
  const vis = (n) => {
    const r = n.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const body = document.getElementById("card-body");
  const nums = (d) =>
    (String(d || "").match(/-?\d+(\.\d+)?/g) || []).map(Number);
  // every in-band and capped line point inside its plot (Q-7924)
  let outside = 0;
  let checked = 0;
  q("svg.sc-svg[data-plot]").forEach((svg) => {
    const [top, bottom] = svg.getAttribute("data-plot").split(" ").map(Number);
    svg.querySelectorAll("path.ml").forEach((p) => {
      const v = nums(p.getAttribute("d"));
      for (let i = 1; i < v.length; i += 2) {
        checked++;
        if (v[i] < top - 0.5 || v[i] > bottom + 0.5) outside++;
      }
    });
  });
  const coloured = q("svg path.ml.sc-line:not(.sc-ink):not(.sc-cap-run)");
  const controls = q("button, a[href], input").filter(vis);
  const ruleAlpha = q(".sc-dt td, .sc-dt th")
    .filter((n) => parseFloat(getComputedStyle(n).borderBottomWidth) > 0)
    .map((n) => {
      const m = getComputedStyle(n).borderBottomColor.match(/[\d.]+/g) || [];
      return m.length > 3 ? Number(m[3]) : 1;
    });
  return {
    text: document.getElementById("root").innerText,
    bodyText: body ? body.innerText.trim() : "",
    layout: document.body.getAttribute("data-layout"),
    title: text(".sc-title"),
    meta: text(".sc-meta"),
    label: text(".sc-label"),
    rows: q(".sc-rows .sc-r").map((r) => [
      r.querySelector(".sc-rk")?.textContent.trim(),
      r.querySelector(".sc-k")?.childNodes[0]?.textContent.trim(),
      r.querySelector(".sc-v")?.textContent.trim(),
    ]),
    firstValueSize: q(".sc-r.sc-first .sc-v").map(
      (n) => getComputedStyle(n).fontSize,
    ),
    barFills: q(".sc-rows .sc-bar i").map(
      (i) => getComputedStyle(i).backgroundColor,
    ),
    newTags: text(".sc-new"),
    tieMore: text(".sc-rows ~ .sc-tie"),
    stripTicks: q(".sc-strip .sc-tk").length,
    stripIds: q(".sc-strip .sc-tk-id").map((n) => n.getAttribute("data-asset")),
    stripLabels: text(".sc-strip .sc-sl"),
    count: text(".sc-count"),
    chips: text(".sc-chip"),
    status: text(".sc-status"),
    err: text(".sc-err"),
    limit: text(".sc-limit"),
    desc: text(".sc-desc"),
    lines: q("svg path.ml.sc-line:not(.sc-cap-run)").length,
    colouredLines: coloured.length,
    colours: [...new Set(coloured.map((p) => getComputedStyle(p).stroke))],
    inkLines: q("svg path.ml.sc-line.sc-ink:not(.sc-cap-run)").length,
    field: q("svg path.sc-field").length,
    capRuns: q("svg path.sc-cap-run").length,
    clips: text("svg .sc-clip"),
    ends: text("svg .sc-end"),
    yTicks: text(".sc-svg .sc-ytick"),
    xTicks: text(".sc-svg .sc-xtick"),
    outside,
    checked,
    dots: q("svg circle.sc-dot").length,
    colouredDots: q("svg circle.sc-dot:not(.sc-out)").length,
    dotLabels: text("svg .sc-dotlab"),
    panels: q(".sc-panel").map((p) => p.getAttribute("data-view") || "dist"),
    controlsBar: text(".sc-controls button"),
    tableRows: q(".sc-dt tbody tr").length,
    tableHeaders: text(".sc-dt thead th"),
    tableFirst: text(".sc-dt tbody tr:first-child td"),
    tiedRanks: q(".sc-dt td.sc-rkc")
      .map((n) => n.textContent.trim())
      .filter((t) => t.endsWith("=")),
    cutNames: q(".sc-dt td.sc-t").filter(
      (n) => n.scrollWidth > n.clientWidth + 1,
    ).length,
    ruleAlpha: ruleAlpha.length ? Math.max(...ruleAlpha) : null,
    readout: text(".sc-read"),
    notes: text(".sc-note"),
    links: q("#link-row .button-link").map((a) => ({
      text: a.textContent.trim(),
      href: a.getAttribute("href"),
      cls: a.className,
    })),
    bareAnchors: q("a").filter((a) => !a.classList.contains("button-link"))
      .length,
    minControl: controls.length
      ? Math.min(
          ...controls.map((n) => Math.round(n.getBoundingClientRect().height)),
        )
      : null,
    isoDate: /\b20\d\d-\d\d-\d\d\b/.test(
      document.getElementById("root").innerText,
    ),
    overflow:
      document.documentElement.scrollWidth >
      document.documentElement.clientWidth + 1,
    height: Math.round(document.documentElement.getBoundingClientRect().height),
  };
};

const browser = await chromium.launch();
const rows = [];
try {
  for (const [name, env] of Object.entries(input.cases))
    for (const mode of modes)
      for (const theme of themes)
        for (const width of mode === "fullscreen" ? [390, 1100] : widths) {
          const full = mode === "fullscreen";
          const html =
            '<!doctype html><html style="color-scheme:' +
            theme +
            '"><body style="margin:0;background:' +
            GROUND[theme] +
            '"><div style="padding:' +
            (full ? 0 : 16) +
            "px;max-width:" +
            width +
            'px"><iframe id="f" style="border:0;width:100%;display:block;' +
            (full ? "height:900px" : "") +
            '"></iframe></div><script>const f=document.getElementById("f");window.addEventListener("message",e=>{const m=e.data;if(!m||m.jsonrpc!=="2.0")return;function send(x){f.contentWindow.postMessage(x,"*")};if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:' +
            JSON.stringify(theme) +
            ",displayMode:" +
            JSON.stringify(mode) +
            ',availableDisplayModes:["inline","fullscreen"],styles:{variables:' +
            JSON.stringify(VARS[theme]) +
            '}}}});else if(m.method==="ui/notifications/initialized")send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:{structuredContent:' +
            JSON.stringify(env).replace(/<\//g, "<\\/") +
            '}});else if(m.method==="ui/notifications/size-changed"){' +
            (full ? "" : 'f.style.height=m.params.height+"px";') +
            '}else if(m.id!==undefined)send({jsonrpc:"2.0",id:m.id,result:{}});});f.srcdoc=' +
            JSON.stringify(input.html).replace(/<\//g, "<\\/") +
            ";</script>";
          const page = await browser.newPage({
            viewport: { width: width + (full ? 0 : 32), height: 940 },
            colorScheme: theme,
          });
          const errors = [];
          page.on("pageerror", (e) => errors.push(e.message));
          await page.setContent(html);
          const frame = await (await page.$("#f")).contentFrame();
          await frame
            .waitForFunction(
              () =>
                !document.querySelector(".skeleton") &&
                document.getElementById("card-body")?.innerText.trim(),
              null,
              { timeout: 5000 },
            )
            .catch(async (error) => {
              throw new Error(
                JSON.stringify({ name, theme, width, mode, errors }) +
                  error.message,
              );
            });
          await page.waitForTimeout(150);
          const row = {
            name,
            theme,
            width,
            mode,
            errors,
            ...(await frame.evaluate(READ)),
          };
          if (full) {
            // Show "All": past the five identity colours a line is ink grey
            const all = await frame.evaluate(() => {
              const b = [
                ...document.querySelectorAll(".sc-controls .sc-seg button"),
              ]
                .filter((n) => /\(\d+\)/.test(n.textContent))
                .pop();
              if (b) b.click();
              return !!b;
            });
            if (all) {
              await page.waitForTimeout(150);
              row.showAll = await frame.evaluate(READ);
            }
            // focus the Table panel by its name
            const focused = await frame.evaluate(() => {
              const b = document.querySelector(
                '.sc-panel[data-view="Table"] .sc-ptitle',
              );
              if (b) b.click();
              return !!b;
            });
            if (focused) {
              await page.waitForTimeout(150);
              row.focusTable = await frame.evaluate(READ);
            }
          }
          const shot = path.join(out, `${name}-${mode}-${theme}-${width}.png`);
          for (let attempt = 1; ; attempt++) {
            try {
              await page.screenshot({ path: shot, fullPage: !full });
              break;
            } catch (error) {
              if (
                attempt >= 3 ||
                !String(error?.message).includes("Unable to capture screenshot")
              )
                throw error;
              await page.waitForTimeout(250);
            }
          }
          rows.push({ ...row, shot });
          await page.close();
        }
  console.log(JSON.stringify({ cases: rows.length, rows }));
} finally {
  await browser.close();
}
