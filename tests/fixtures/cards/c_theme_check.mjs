/* Q-7890: the card follows the HOST's theme, never the OS's.
 *
 * Draws one served card (`build_card_html(kind)`) with one real envelope in
 * headless Chromium, once per (OS scheme × host theme × dialect), inside an
 * embedder page that behaves as a host does: its root declares the host's
 * color-scheme and paints the host's ground. For each it reports what a
 * person sees:
 *   - `ground`: the pixel behind the card (a blank corner of the card): it is
 *     the embedder's ground only when the iframe's canvas stays transparent;
 *   - `title`: the card title's computed colour, and its contrast against
 *     that pixel;
 *   - `scheme`: the card root's computed color-scheme.
 * Prints ONE JSON object; tests/test_card_theme_browser.py asserts.
 *
 * Usage: node c_theme_check.mjs --card <card.html> --envelope <result.json>
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

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}
const card = fs.readFileSync(arg("card"), "utf8");
const res = JSON.parse(fs.readFileSync(arg("envelope"), "utf8"));
// Each host's ground: Claude's documented color-background-primary (design-guidelines), and ChatGPT's reference
// dark surface (#212121, its submission rules) behind the legacy window.openai dialect.
const GROUNDS = {
  mcp: { light: "#ffffff", dark: "#30302e" },
  openai: { light: "#ffffff", dark: "#212121" },
};
const TEXT = { light: "#141413", dark: "#faf9f5" };

function embedder(host, dialect) {
  const vars = {
    "--color-background-secondary": host === "dark" ? "#262624" : "#f5f4ed",
    "--color-text-primary": TEXT[host],
    "--color-text-secondary": host === "dark" ? "#c2c0b6" : "#3d3d3a",
    "--color-border-primary":
      host === "dark" ? "rgba(222,220,209,.4)" : "rgba(31,30,29,.4)",
  };
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
<style>body{margin:0;background:${ground}}iframe{border:0;width:760px;height:700px;display:block}</style></head>
<body><iframe id="c"></iframe><script>
const f=document.getElementById("c");const res=${JSON.stringify(res).replace(/<\//g, "<\\/")};
window.addEventListener("message",(ev)=>{if(ev.source!==f.contentWindow)return;const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
 const send=(x)=>f.contentWindow.postMessage(x,"*");
 if(m.method==="ui/initialize")send({jsonrpc:"2.0",id:m.id,result:{protocolVersion:"2026-01-26",hostContext:{theme:${JSON.stringify(host)},displayMode:"inline",availableDisplayModes:["inline","fullscreen"],styles:{variables:${JSON.stringify(vars)}}}}});
 else if(m.method==="ui/notifications/initialized")setTimeout(()=>send({jsonrpc:"2.0",method:"ui/notifications/tool-result",params:res}),40);
 else if(m.id!==undefined)send({jsonrpc:"2.0",id:m.id,result:{}});});
f.srcdoc=${JSON.stringify(doc).replace(/<\//g, "<\\/")};
</script></body></html>`;
}

function hex(rgb) {
  return (
    "#" +
    rgb
      .slice(0, 3)
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("")
  );
}
function lum(rgb) {
  const c = rgb.slice(0, 3).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const browser = await chromium.launch();
const out = { arms: [] };
for (const os of ["light", "dark"])
  for (const host of ["light", "dark"])
    for (const dialect of ["mcp", "openai"]) {
      const page = await browser.newPage({
        viewport: { width: 800, height: 760 },
        colorScheme: os,
      });
      await page.setContent(embedder(host, dialect));
      const frame = await (await page.$("#c")).contentFrame();
      await frame.waitForFunction(
        () =>
          document.getElementById("card-body") &&
          !document.getElementById("card-body").hasAttribute("aria-busy"),
        null,
        { timeout: 8000 },
      );
      await page.waitForTimeout(300);
      const probe = await frame.evaluate(() => {
        const t =
          document.querySelector("#card-head .head-name") ||
          document.querySelector("#card-head .card-title");
        const cs = t ? getComputedStyle(t).color : "";
        const m = cs.match(/\d+(\.\d+)?/g) || [];
        return {
          scheme: getComputedStyle(document.documentElement).colorScheme,
          theme: document.documentElement.getAttribute("data-theme"),
          title: m.slice(0, 3).map((v) => Math.round(Number(v))),
          titleText: t ? t.textContent.trim().slice(0, 40) : null,
          // non-vacuity: the card drew its result (a tile, a chart or a row),
          // never the adapter's empty state
          drawn: document.querySelectorAll(
            "#card-body .tile, #card-body svg, #card-body canvas, #card-body .row, #card-body tr, #card-body .sf-tile",
          ).length,
          empty: /No result to show here/.test(
            (document.getElementById("card-body") || document.body)
              .textContent || "",
          ),
        };
      });
      // the pixel behind the card: the iframe's bottom-right corner, below
      // the content, where nothing of the card is drawn
      const png = await page.screenshot({
        clip: { x: 740, y: 680, width: 4, height: 4 },
      });
      const px = await page.evaluate(async (b64) => {
        const img = new Image();
        img.src = "data:image/png;base64," + b64;
        await img.decode();
        const c = document.createElement("canvas");
        c.width = img.width;
        c.height = img.height;
        const x = c.getContext("2d");
        x.drawImage(img, 0, 0);
        return Array.from(x.getImageData(1, 1, 1, 1).data);
      }, png.toString("base64"));
      out.arms.push({
        os,
        host,
        dialect,
        ground: hex(px),
        expected: GROUNDS[dialect][host],
        scheme: probe.scheme,
        theme: probe.theme,
        title: probe.title.length ? hex(probe.title) : null,
        titleText: probe.titleText,
        drawn: probe.drawn,
        empty: probe.empty,
        contrast: probe.title.length
          ? Math.round(contrast(probe.title, px) * 100) / 100
          : null,
      });
      await page.close();
    }
await browser.close();
console.log(JSON.stringify(out));
