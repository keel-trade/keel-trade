// L13 host checks for the screen card in an MCP Apps host (srcdoc iframe,
// ui/* bridge): the action link goes through ui/open-link (D-1), the
// fullscreen end labels stay on the plot in their lines' order (D-3), the
// expand target is a button (D-4), the number buttons name the coin first
// (D-6), the table's sort is a keyboard button (D-5), and a run in flight
// says so (D-2). Input: {html, envs: {view, inflight}}. Prints one JSON line.
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

function host(env, mode, tab) {
  return (
    '<!doctype html><body style="margin:0"><iframe id="f" sandbox="allow-scripts" style="border:0;width:100%"></iframe><script>window.msgs=[];const f=document.getElementById("f");window.addEventListener("message",e=>{const m=e.data;if(!m||m.jsonrpc!=="2.0")return;window.msgs.push(m);function send(x){f.contentWindow.postMessage(x,"*")};if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:"light",displayMode:' +
    JSON.stringify(mode) +
    ',availableDisplayModes:["inline","fullscreen"]}}});if(m.method==="ui/notifications/initialized")send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:{structuredContent:' +
    JSON.stringify(env).replace(/<\//g, "<\\/") +
    '}});if(m.id!=null&&m.method&&m.method!=="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{}});if(m.method==="ui/notifications/size-changed")f.style.height=m.params.height+"px";});f.srcdoc=' +
    JSON.stringify(input.html).replace(/<\//g, "<\\/") +
    ";</script>"
  );
}

const browser = await chromium.launch();
const out = {};
try {
  async function open(env, mode, height) {
    const page = await browser.newPage({ viewport: { width: 900, height } });
    const popups = [];
    page.on("popup", (p) => popups.push(p.url()));
    await page.setContent(host(env, mode));
    const frame = page.frames().find((f) => f !== page.mainFrame());
    await frame.waitForFunction(
      () => document.getElementById("card-body")?.innerText.trim(),
      null,
      { timeout: 5000 },
    );
    return { page, frame, popups };
  }

  // Inline: Expand is a visible button (Q-7926), and the end labels name
  // the coin first. In Expand: Test as a strategy is the quiet button, and
  // it goes through ui/open-link (D-1), never a popup.
  {
    const { page, frame } = await open(input.envs.view, "inline", 1200);
    const expand = frame.locator("#link-row a.button-link", {
      hasText: "Expand",
    });
    assert.equal(await expand.count(), 1, "a visible Expand");
    const names = await frame.$$eval("svg text.sc-end", (ts) =>
      ts.map((t) => [t.textContent, t.getAttribute("data-asset")]),
    );
    assert.ok(names.length >= 2, "end labels drawn");
    for (const [label, asset] of names)
      assert.ok(label && label.includes(asset + " "), "coin first: " + label);
    out.inline = { numbers: names.length };
    await page.close();
  }
  {
    const { page, frame, popups } = await open(
      input.envs.view,
      "fullscreen",
      1200,
    );
    const action = frame.locator("#link-row a.sc-test");
    assert.equal(await action.count(), 1, "one extra action");
    assert.ok(
      (await action.getAttribute("class")).includes("button-link"),
      "the extra action is a button, never a bare link (Q-7920)",
    );
    const href = await action.getAttribute("href");
    assert.equal(await action.getAttribute("target"), null, "no target=_blank");
    await action.click();
    await page.waitForTimeout(200);
    const links = await page.evaluate(() =>
      window.msgs
        .filter((m) => m.method === "ui/open-link")
        .map((m) => m.params.url),
    );
    assert.deepEqual(links, [href], "the action goes through ui/open-link");
    assert.deepEqual(popups, [], "no popup");
    out.inline.links = links.length;
    await page.close();
  }

  // Fullscreen, short: the labels stay on the plot, in their lines' order.
  {
    const { page, frame } = await open(input.envs.view, "fullscreen", 360);
    const all = frame.getByRole("button", { name: /^All \(/ });
    if (await all.count()) await all.first().click();
    await page.waitForTimeout(200);
    const geo = await frame.evaluate(() => {
      const svg = document.querySelector(".sc-chart svg[data-plot]");
      const [top, bottom] = svg
        .getAttribute("data-plot")
        .split(" ")
        .map(Number);
      const labels = [...svg.querySelectorAll("text.sc-end")].map((t) => ({
        y: Number(t.getAttribute("y")),
        text: t.textContent,
      }));
      return {
        top,
        bottom,
        labels,
        lines: svg.querySelectorAll("path.ml.sc-line:not(.sc-cap-run)").length,
      };
    });
    assert.ok(geo.lines >= 10, "a crowded chart: " + geo.lines);
    assert.ok(geo.labels.length >= 2, "labels drawn");
    assert.ok(
      geo.labels.length <= Math.floor((geo.bottom - geo.top) / 13) + 1,
      "capped",
    );
    for (const l of geo.labels)
      assert.ok(
        l.y >= geo.top && l.y <= geo.bottom + 1,
        `label ${l.text} at ${l.y} outside ${geo.top}..${geo.bottom}`,
      );
    out.fullscreen = { labels: geo.labels.length, lines: geo.lines };
    // The table's sort is reachable from the keyboard.
    const table = frame.getByRole("button", { name: "Table", exact: true });
    if (await table.count()) {
      await table.first().click();
      await page.waitForTimeout(150);
      const sorts = await frame.locator("th button.sc-sort").count();
      assert.ok(sorts >= 1, "sort buttons in the table header");
      out.sort_buttons = sorts;
    }
    await page.close();
  }

  // A stored passing list shorter than the passing count (the first 50 by
  // name) is not the passing set: the ranked numbers still show.
  if (input.envs.truncated) {
    const { page, frame } = await open(input.envs.truncated, "inline", 1200);
    const nums = await frame.locator("svg text.sc-end").count();
    assert.ok(nums >= 1, "named lines despite a truncated passing list");
    out.truncated_nums = nums;
    await page.close();
  }

  // A Scatter with no coin carrying both values draws no NaN geometry
  // (staging 2026-11-03: a funding gap at the latest bar).
  if (input.envs.empty_scatter) {
    const { page, frame } = await open(input.envs.empty_scatter, "inline", 900);
    const nan = await frame.evaluate(
      () =>
        [
          ...document.querySelectorAll("svg [x], svg [y], svg [cx], svg [cy]"),
        ].filter((n) =>
          ["x", "y", "cx", "cy"].some((a) => n.getAttribute(a) === "NaN"),
        ).length,
    );
    assert.equal(nan, 0, "no NaN geometry in an empty Scatter");
    out.empty_scatter = { nan };
    await page.close();
  }

  // A run in flight says it is running on the card.
  {
    const { page, frame } = await open(input.envs.inflight, "inline", 600);
    const text = await frame.locator("#card-body").innerText();
    assert.ok(/running/i.test(text), "in-flight words on the card: " + text);
    out.inflight = true;
    await page.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify(out));
