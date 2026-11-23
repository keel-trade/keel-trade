/* D-420d: one shared button pair on every card, both profiles.
 *
 * Draws several served cards (`{cards: [{name, html, envelope}]}`) in
 * headless Chromium, once per (host theme × OS scheme × dialect × width),
 * inside an embedder that behaves as a host does (its root declares the
 * host's color-scheme and paints the host's ground, Claude's documented
 * tokens through the MCP Apps bridge, or the legacy `window.openai` globals),
 * and reports every link-row action: its words, whether it is the primary
 * (`button-primary`, or the row's first link that opens a URL), its computed
 * fill, ink and border, its height, and the ground behind the card.
 * Prints ONE JSON object; tests/test_card_button_pair.py asserts.
 *
 * Usage: node c_button_pair_check.mjs <input.json>
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
const GROUNDS = {
  mcp: { light: "#ffffff", dark: "#30302e" },
  openai: { light: "#ffffff", dark: "#212121" },
};
const VARS = {
  light: {
    "--color-background-secondary": "#f5f4ed",
    "--color-text-primary": "#141413",
    "--color-text-secondary": "#3d3d3a",
    "--color-border-primary": "rgba(31,30,29,.4)",
    "--color-border-tertiary": "rgba(31,30,29,.15)",
  },
  dark: {
    "--color-background-secondary": "#262624",
    "--color-text-primary": "#faf9f5",
    "--color-text-secondary": "#c2c0b6",
    "--color-border-primary": "rgba(222,220,209,.4)",
    "--color-border-tertiary": "rgba(222,220,209,.15)",
  },
};

function embedder(card, res, host, dialect, width) {
  const shim =
    dialect === "openai"
      ? "<script>window.openai={theme:" +
        JSON.stringify(host) +
        ",displayMode:'inline',maxHeight:2000,callTool:function(){return Promise.resolve({})}," +
        "openExternal:function(){},sendFollowUpMessage:function(){},requestDisplayMode:function(a){return Promise.resolve(a)}," +
        "notifyIntrinsicHeight:function(){},toolInput:{},toolOutput:" +
        JSON.stringify(res.structuredContent || {}).replace(/<\//g, "<\\/") +
        ",toolResponseMetadata:" +
        JSON.stringify(res._meta || {}).replace(/<\//g, "<\\/") +
        "};</script>"
      : "";
  const doc =
    dialect === "openai"
      ? card.replace(/<body([^>]*)>/, (_m, a) => `<body${a}>${shim}`)
      : card;
  const ground = GROUNDS[dialect][host];
  return `<!doctype html><html style="color-scheme:${host};background:${ground}"><head><meta charset="utf-8">
<style>body{margin:0;background:${ground}}iframe{border:0;width:${width}px;height:900px;display:block}</style></head>
<body><iframe id="c"></iframe><script>
const f=document.getElementById("c");const res=${JSON.stringify(res).replace(/<\//g, "<\\/")};
window.addEventListener("message",(ev)=>{if(ev.source!==f.contentWindow)return;const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 const send=(x)=>f.contentWindow.postMessage(x,"*");
 if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:${JSON.stringify(host)},displayMode:"inline",availableDisplayModes:["inline","fullscreen"],styles:{variables:${JSON.stringify(VARS[host])}}}}});
 else if(m.method==="ui/notifications/initialized")setTimeout(()=>send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:res}),40);
 else if(m.id!==undefined)send({jsonrpc:"2.0",id:m.id,result:{}});});
f.srcdoc=${JSON.stringify(doc).replace(/<\//g, "<\\/")};
</script></body></html>`;
}

const ARMS = input.arms || [
  ["light", "light", "mcp", 760],
  ["dark", "dark", "mcp", 760],
  ["light", "dark", "mcp", 760],
  ["dark", "light", "mcp", 760],
  ["light", "light", "openai", 760],
  ["dark", "dark", "openai", 760],
  ["light", "light", "mcp", 380],
];
const browser = await chromium.launch();
const out = { arms: [] };
try {
  for (const c of input.cards)
    for (const [host, os, dialect, width] of ARMS) {
      const page = await browser.newPage({
        viewport: { width: width + 40, height: 940 },
        colorScheme: os,
      });
      await page.setContent(embedder(c.html, c.envelope, host, dialect, width));
      const frame = await (await page.$("#c")).contentFrame();
      await frame.waitForFunction(
        () =>
          document.getElementById("card-body") &&
          !document.getElementById("card-body").hasAttribute("aria-busy"),
        null,
        { timeout: 8000 },
      );
      await page.waitForTimeout(200);
      const buttons = await frame.evaluate(() =>
        [...document.querySelectorAll("#link-row .button-link")].map((b, i) => {
          const s = getComputedStyle(b);
          const r = b.getBoundingClientRect();
          return {
            text: b.textContent.trim(),
            index: i,
            classes: b.className,
            href: b.getAttribute("href"),
            bg: s.backgroundColor,
            fg: s.color,
            border: s.borderTopColor,
            radius: s.borderTopLeftRadius,
            font: s.fontSize + "/" + s.fontWeight,
            h: Math.round(r.height),
          };
        }),
      );
      out.arms.push({
        card: c.name,
        host,
        os,
        dialect,
        width,
        ground: GROUNDS[dialect][host],
        buttons,
      });
      await page.close();
    }
} finally {
  await browser.close();
}
console.log(JSON.stringify(out));
