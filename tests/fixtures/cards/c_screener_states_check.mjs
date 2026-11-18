import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(
  path.resolve(here, "../../../../../../services/keel-app/package.json"),
);
const { chromium } = require("playwright");
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const browser = await chromium.launch();
const rows = [];
try {
  for (const [state, env] of Object.entries(input.states))
    for (const dialect of ["openai", "mcp"])
      for (const width of [320, 900]) {
        const mode = width === 900 ? "fullscreen" : "inline";
        let html;
        if (dialect === "openai") {
          const shim =
            '<script>window.openai={theme:"dark",displayMode:' +
            JSON.stringify(mode) +
            ",maxHeight:1000,toolOutput:" +
            JSON.stringify(env).replace(/<\//g, "<\\/") +
            ',callTool(){return Promise.resolve({})},notifyIntrinsicHeight(){},requestDisplayMode(){return Promise.resolve({mode:"fullscreen"})},openExternal(){}};</script>';
          html = input.html.replace(
            /<body([^>]*)>/,
            (_m, attrs) => "<body" + attrs + ">" + shim,
          );
        } else {
          html =
            '<!doctype html><body><iframe id="f" style="border:0;width:100%"></iframe><script>const f=document.getElementById("f");window.addEventListener("message",e=>{const m=e.data;if(!m||m.jsonrpc!=="2.0")return;function send(x){f.contentWindow.postMessage(x,"*")};if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:"light",displayMode:' +
            JSON.stringify(mode) +
            ',availableDisplayModes:["inline","fullscreen"]}}});if(m.method==="ui/notifications/initialized")send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:{structuredContent:' +
            JSON.stringify(env).replace(/<\//g, "<\\/") +
            '}});if(m.method==="ui/notifications/size-changed")f.style.height=m.params.height+"px";});f.srcdoc=' +
            JSON.stringify(input.html).replace(/<\//g, "<\\/") +
            ";</script>";
        }
        const page = await browser.newPage({
          viewport: { width, height: 1200 },
        });
        const errors = [];
        page.on("pageerror", (e) => errors.push(e.message));
        await page.setContent(html);
        const frame =
          dialect === "mcp"
            ? page.frames().find((f) => f !== page.mainFrame())
            : page.mainFrame();
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
                state,
                dialect,
                width,
                errors,
                text: await frame.locator("body").innerText(),
              }) + error.message,
            );
          });
        const read = () =>
          frame.evaluate(() => ({
            text: document.getElementById("root").innerText,
            paths: document.querySelectorAll(".chart path.ml").length,
            field: document.querySelectorAll(".chart path.sc-field").length,
            dim: document.querySelectorAll(".chart path.ml.dim").length,
            ends: document.querySelectorAll(".chart text.sc-end").length,
            tabs: document.querySelectorAll(".sc-tabs button").length,
            nums: document.querySelectorAll(".sc-num").length,
            strokes: [...document.querySelectorAll(".chart path.ml")].map((p) =>
              p.getAttribute("stroke"),
            ),
            links: document.querySelectorAll("a").length,
            dots: document.querySelectorAll(".sc-chart circle.sc-dot").length,
            dotFills: [
              ...document.querySelectorAll(".sc-chart circle.sc-dot:not(.out)"),
            ].map((c) => c.getAttribute("fill")),
            rows: document.querySelectorAll(".sc-dt tbody tr").length,
            sel: document.querySelectorAll(".sc-dt tbody tr.sel").length,
            overflow:
              document.documentElement.scrollWidth >
              document.documentElement.clientWidth,
          }));
        const result = await read();
        const ctx = JSON.stringify({ state, dialect, width, result });
        const expect = (input.expect || {})[state] || {};
        const full = mode === "fullscreen";
        assert.deepEqual(errors, []);
        assert.ok(
          result.text.includes("Golden screen") ||
            result.text.includes("unavailable"),
          ctx,
        );
        if (expect.lines) {
          // inline: the top 5 by rank coloured and labelled, the rest field;
          // fullscreen: every shipped line coloured (Show: All)
          assert.equal(result.paths, full ? expect.lines : 5, ctx);
          assert.equal(result.field, full ? 0 : expect.lines - 5, ctx);
          assert.equal(result.ends, full ? expect.lines : 5, ctx);
          assert.ok(
            result.strokes.every((s) => /^var\(--sc-\d+\)$/.test(s)),
            ctx,
          );
          assert.equal(
            new Set(result.strokes).size,
            result.strokes.length,
            ctx,
          );
          assert.ok(result.nums >= 1 && result.nums <= 5, ctx);
          // tap a number: its line stays, every other dims; tap again clears
          await frame.locator(".sc-num").first().click();
          const iso = await read();
          assert.equal(iso.dim, iso.paths - 1, JSON.stringify(iso));
          await frame.locator(".sc-num").first().click();
          assert.equal((await read()).dim, 0);
        } else assert.equal(result.paths, 0, ctx);
        // a Scatter or Table default draws that graph inline, colouring
        // exactly the passing coins (L6 round 4, founder: specific cards)
        if (expect.dots != null) {
          assert.equal(result.dots, expect.dots, ctx);
          assert.equal(result.dotFills.length, expect.passing, ctx);
          assert.ok(
            result.dotFills.every((f) => /^var\(--sc-\d+\)$/.test(f)),
            ctx,
          );
        }
        if (expect.rows != null) {
          assert.equal(result.rows, expect.rows, ctx);
          assert.equal(result.sel, expect.passing, ctx);
        }
        if (expect.nums != null) assert.equal(result.nums, expect.nums, ctx);
        for (const word of expect.present || [])
          assert.ok(result.text.includes(word), word + " " + ctx);
        for (const word of expect.absent || [])
          assert.ok(!result.text.includes(word), word + " " + ctx);
        if (expect.tabs != null)
          assert.equal(result.tabs, full ? expect.tabs : 0, ctx);
        if (expect.tabs && full) {
          await frame.locator(".sc-tabs button", { hasText: "Table" }).click();
          const rows = await frame.locator(".sc-dt tbody tr").count();
          assert.equal(rows, expect.tableRows, ctx);
          await frame.locator(".sc-dt th").nth(1).click(); // sort toggles
          await frame
            .locator(".sc-tabs button", { hasText: "Scatter" })
            .click();
          const dots = await frame.locator(".sc-chart circle.sc-dot").count();
          assert.equal(dots, expect.tableRows, ctx);
          await frame.locator(".sc-tabs button", { hasText: "Lines" }).click();
          assert.equal(await frame.locator(".sc-slider").count(), 1, ctx);
          assert.ok(!(await read()).overflow, ctx);
        }
        assert.ok(!result.overflow, ctx);
        assert.ok(result.links <= 2);
        rows.push({ state, dialect, width, ...result });
        await page.screenshot({
          path: path.join(
            path.dirname(process.argv[2]),
            `screener-${state}-${dialect}-${width}.png`,
          ),
        });
        await page.close();
      }
  console.log(JSON.stringify({ cases: rows.length, rows }));
} finally {
  await browser.close();
}
