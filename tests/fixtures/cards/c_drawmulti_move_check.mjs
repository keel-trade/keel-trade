/* Byte-identical SVG before/after the shared renderer move, over size/DD arms. */
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const here = path.dirname(fileURLToPath(import.meta.url));
const assets = path.resolve(here, "../../../keel/widgets/assets");
class Node {
  constructor(tag) {
    this.tag = tag;
    this.attrs = {};
    this.children = [];
    this._text = "";
    this.clientWidth = 600;
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  set textContent(value) {
    this._text = value;
    this.children = [];
  }
  get textContent() {
    return this._text;
  }
  get outerHTML() {
    return (
      "<" +
      this.tag +
      Object.entries(this.attrs)
        .map(([k, v]) => " " + k + '="' + v + '"')
        .join("") +
      ">" +
      this._text +
      this.children.map((c) => c.outerHTML).join("") +
      "</" +
      this.tag +
      ">"
    );
  }
}
const H = {
  fmt: { drawdown: (v) => ({ text: String(v) }) },
  fmtPeriod: (a, b) => a.toISOString() + "/" + b.toISOString(),
  fmtAxisValues: (xs) => xs.map(String),
  fmtAxisDates: (xs) => xs.map(String),
};
const context = {
  window: { KeelHost: H },
  document: { createElementNS: (_, tag) => new Node(tag) },
  Date,
  Math,
};
const before = vm.runInNewContext(
  fs.readFileSync(path.join(here, "c_drawmulti_pre_move.js"), "utf8"),
  context,
);
const adapter = fs.readFileSync(path.join(assets, "host-adapter.js"), "utf8");
const start =
  adapter.indexOf("KeelHost.drawMulti = ") + "KeelHost.drawMulti = ".length;
const end =
  adapter.indexOf(";", adapter.indexOf("})(window.KeelHost)", start)) + 1;
const after = vm.runInNewContext(adapter.slice(start, end), context);
const series = [
  {
    label: "A",
    base: true,
    stroke: "red",
    pts: [
      { t: 0, equity: 10, dd: 0 },
      { t: 1000, equity: 20, dd: -1 },
    ],
  },
  {
    label: "B",
    stroke: "blue",
    pts: [
      { t: 100, equity: 15, dd: 0 },
      { t: 900, equity: 12, dd: -2 },
    ],
  },
];
let cases = 0;
for (const width of [320, 720, 1200])
  for (const dd of [false, true])
    for (const tall of [false, true]) {
      const a = new Node("div"),
        b = new Node("div");
      const opts = { dd, tall, overlap: { t0: 100, t1: 900 } };
      before(a, series, opts, width);
      after(b, series, opts, width);
      assert.equal(a.outerHTML, b.outerHTML);
      cases++;
    }
const gap = new Node("div");
after(
  gap,
  [
    {
      label: "gap",
      stroke: "red",
      pts: [
        { t: 0, equity: 1, dd: 0 },
        { t: 1, equity: null, dd: 0 },
        { t: 2, equity: 3, dd: 0 },
      ],
    },
  ],
  { gaps: true, dd: false },
  320,
);
const line = gap.children[0].children.find((c) => c.tag === "path");
assert.equal((line.attrs.d.match(/M/g) || []).length, 2);
console.log(JSON.stringify({ unchanged_svg_cases: cases, gap_segments: 2 }));
