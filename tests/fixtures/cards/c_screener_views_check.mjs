// The screen card drawn from its SAVED VIEW (Q-3635): every fixture case in
// the input, in an MCP Apps host (claude.ai's shape), light and dark, at a
// phone width and a wide one, inline. Writes one screenshot per rendering
// into `input.out` and prints what the card drew, for the Python test to
// judge. Input: {html, cases: {name: envelope}, out, widths?, themes?}.
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
const out = input.out || path.dirname(process.argv[2]);
fs.mkdirSync(out, { recursive: true });
// The hosts' own grounds (tests/test_card_contrast.py GROUNDS), so a
// screenshot shows the card as claude.ai paints it.
const GROUND = { light: "#faf9f5", dark: "#1f1e1d" };
const browser = await chromium.launch();
const rows = [];
try {
  for (const [name, env] of Object.entries(input.cases))
    for (const theme of themes)
      for (const width of widths) {
        const html =
          '<!doctype html><body style="margin:0;background:' +
          GROUND[theme] +
          '"><div style="padding:16px;max-width:' +
          width +
          'px"><iframe id="f" style="border:0;width:100%;display:block"></iframe></div><script>const f=document.getElementById("f");window.addEventListener("message",e=>{const m=e.data;if(!m||m.jsonrpc!=="2.0")return;function send(x){f.contentWindow.postMessage(x,"*")};if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:' +
          JSON.stringify(theme) +
          ',displayMode:"inline",availableDisplayModes:["inline","fullscreen"]}}});if(m.method==="ui/notifications/initialized")send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:{structuredContent:' +
          JSON.stringify(env).replace(/<\//g, "<\\/") +
          '}});if(m.method==="ui/notifications/size-changed")f.style.height=m.params.height+"px";});f.srcdoc=' +
          JSON.stringify(input.html).replace(/<\//g, "<\\/") +
          ";</script>";
        const page = await browser.newPage({
          viewport: { width: width + 32, height: 900 },
          colorScheme: theme,
        });
        const errors = [];
        page.on("pageerror", (e) => errors.push(e.message));
        await page.setContent(html);
        const frame = page.frames().find((f) => f !== page.mainFrame());
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
              JSON.stringify({
                name,
                theme,
                width,
                errors,
                text: await frame.locator("body").innerText(),
              }) + error.message,
            );
          });
        await page.waitForTimeout(120);
        const result = await frame.evaluate(() => {
          const q = (s) => [...document.querySelectorAll(s)];
          const text = (s) => q(s).map((n) => n.textContent.trim());
          const body = document.getElementById("card-body");
          return {
            text: document.getElementById("root").innerText,
            bodyText: body ? body.innerText.trim() : "",
            headName: document.querySelector(".head-name")?.textContent ?? "",
            headFont: document.querySelector(".head-name")
              ? getComputedStyle(document.querySelector(".head-name"))
                  .fontFamily
              : "",
            meta: text(".sc-meta"),
            pass: text(".sc-pass"),
            tie: text(".sc-tie"),
            tableRows: q(".sc-dt tbody tr").length,
            headers: text(".sc-dt thead th"),
            firstRow: text(".sc-dt tbody tr:first-child td"),
            rows: q(".sc-dt tbody tr").map((tr) =>
              [...tr.querySelectorAll("td")].map((td) => td.textContent.trim()),
            ),
            // the ranked output's range line (Q-3640)
            range: text(".sc-range"),
            // the body's blocks in order, by their last `sc-` class
            order: [...body.children].map(
              (n) =>
                [...n.classList].filter((c) => c.startsWith("sc-")).pop() ||
                n.tagName.toLowerCase(),
            ),
            bars: q(".sc-bar").length,
            divBars: q(".sc-bar.div").length,
            barFills: q(".sc-bar i").map((i) => getComputedStyle(i).background),
            nums: q(".sc-num").length,
            numLabel: text(".sc-numlab"),
            numValues: text(".sc-num .v"),
            lines: q(".chart path.ml").length,
            field: q(".chart path.sc-field").length,
            strokes: q(".chart path.ml").map((p) => p.getAttribute("stroke")),
            yTicks: text(".sc-svg .sc-ytick"),
            xTicks: text(".sc-svg .sc-xtick"),
            clips: q(".sc-svg .sc-clip").length,
            ends: text(".sc-svg .sc-end"),
            more: text(".sc-more"),
            caps: text(".sc-cap"),
            receipt: text(".receipt"),
            links: q("a").length,
            ellipsis: (
              document.getElementById("root").innerText.match(/…/g) || []
            ).length,
            overflow:
              document.documentElement.scrollWidth >
              document.documentElement.clientWidth,
            // a cell whose text wraps onto a second line
            wrapped: q(".sc-num, .sc-dt td, .sc-dt th").filter(
              (n) => n.getClientRects().length && n.offsetHeight > 40,
            ).length,
            height: document.documentElement.scrollHeight,
          };
        });
        const shot = path.join(out, `${name}-${theme}-${width}.png`);
        await page.screenshot({ path: shot, fullPage: true });
        rows.push({ name, theme, width, errors, shot, ...result });
        await page.close();
      }
  console.log(JSON.stringify({ cases: rows.length, rows }));
} finally {
  await browser.close();
}
