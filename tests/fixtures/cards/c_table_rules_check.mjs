/* D-420a: tables never draw white lines.
 *
 * Draws several served cards (`{cards: [{name, html, envelope}], arms?}`) in
 * headless Chromium inside an embedder that behaves as a host does (its root
 * declares the host's color-scheme and paints the host's ground; Claude's
 * documented tokens through the MCP Apps bridge, primary border 40 %,
 * tertiary 15 %), and reports every visible table rule: the bottom border of
 * each table cell and each per-row list item, its computed colour and width.
 * Prints ONE JSON object; tests/test_card_table_rules.py asserts.
 *
 * Usage: node c_table_rules_check.mjs <input.json>
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
const { chromium } = require("playwright");

const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
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

function embedder(card, res, host, width) {
  const ground = GROUND[host];
  return `<!doctype html><html style="color-scheme:${host};background:${ground}"><head><meta charset="utf-8">
<style>body{margin:0;background:${ground}}iframe{border:0;width:${width}px;height:1400px;display:block}</style></head>
<body><iframe id="c"></iframe><script>
const f=document.getElementById("c");const res=${JSON.stringify(res).replace(/<\//g, "<\\/")};
window.addEventListener("message",(ev)=>{if(ev.source!==f.contentWindow)return;const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 const send=(x)=>f.contentWindow.postMessage(x,"*");
 if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:${JSON.stringify(host)},displayMode:"inline",availableDisplayModes:["inline","fullscreen"],styles:{variables:${JSON.stringify(VARS[host])}}}}});
 else if(m.method==="ui/notifications/initialized")setTimeout(()=>send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:res}),40);
 else if(m.id!==undefined)send({jsonrpc:"2.0",id:m.id,result:{}});});
f.srcdoc=${JSON.stringify(card).replace(/<\//g, "<\\/")};
</script></body></html>`;
}

const ARMS = input.arms || [
  ["light", 760],
  ["dark", 760],
  ["dark", 380],
];
const browser = await chromium.launch();
const out = { arms: [] };
try {
  for (const c of input.cards)
    for (const [host, width] of ARMS) {
      const page = await browser.newPage({
        viewport: { width: width + 40, height: 1440 },
        colorScheme: host,
      });
      await page.setContent(embedder(c.html, c.envelope, host, width));
      const frame = await (await page.$("#c")).contentFrame();
      await frame.waitForFunction(
        () =>
          document.getElementById("card-body") &&
          !document.getElementById("card-body").hasAttribute("aria-busy"),
        null,
        { timeout: 8000 },
      );
      await page.waitForTimeout(200);
      const rules = await frame.evaluate(() =>
        [
          ...document.querySelectorAll(
            "table td, table th, .row-item, .row-head",
          ),
        ]
          .filter((n) => {
            const r = n.getBoundingClientRect();
            const s = getComputedStyle(n);
            return (
              r.width > 0 &&
              r.height > 0 &&
              parseFloat(s.borderBottomWidth) > 0 &&
              s.borderBottomStyle !== "none"
            );
          })
          .map((n) => ({
            sel: n.tagName.toLowerCase() + "." + (n.className || ""),
            color: getComputedStyle(n).borderBottomColor,
          })),
      );
      const text = await frame.evaluate(
        () => document.getElementById("card-body").innerText,
      );
      out.arms.push({
        card: c.name,
        host,
        width,
        ground: GROUND[host],
        rules,
        drew: text.trim().length,
      });
      await page.close();
    }
} finally {
  await browser.close();
}
console.log(JSON.stringify(out));
