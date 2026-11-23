/* Study card (research spec 13 §2; 07 §3.3): drawn only from the envelope
   (`card_summary` in structuredContent, `card_data` from `_meta["keel/card"]`),
   never a fetch and never a computed number (A13).

   The layout is the served `view.mode` (13 §2.1), never a sniffed shape:
   - study: header · lead · the key chart (served CardChart, else the thumb,
     else the top rows) · the numbers · footing · the strip of more charts.
   - run: progress words, a bar, the partial key chart or the steps, the time
     words; a finished run inside the wait draws the compact result.
   - plan: the shared receipt row; "Show steps" opens the step list in place.
   - list: up to five studies, each a link.
   - chart: that one chart at card size and the rows words.
   - addresses, project, library: the shared receipt row.
   - a refusal (error envelope): the served card words and one link.
   An envelope with no `view.mode` (a v1 server) draws the study summary from
   `card_summary` alone; a `card_data` whose schema major is not 2 is ignored
   (13 §4.4). Fullscreen draws the tabs, "Your checks" and "The numbers".

   Every slot has a fixed height per width band (card-study.css, 13 §4.2).
   Every word comes from the envelope: this file carries no English and
   formats no number (13 §7.1's source guard); coordinates are geometry. */
(function () {
  "use strict";
  var H = window.KeelHost;
  var el = H.el;
  var NS = "http://www.w3.org/2000/svg";
  var TPL = "study/2";
  var CARD_DATA_MAJOR = "2";
  var lastEnv = null;
  var lastMode = null;
  var lastNarrow = null;
  var superseded = false;
  var MAX_NUMBERS = 4;
  var MAX_ROWS = 5;
  var MAX_NOTES = 2;
  var MAX_STEPS = 5;
  var MAX_LIST = 5;
  var MAX_CHECKS = 40;
  var MAX_ISSUES = 3;

  // The thumb's coordinate box (research.card_summary: x columns 0-299, y rows
  // 0-63, 63 at the top).
  var TW = 300;
  var TH = 64;
  // A CardChart's geometry (13 §3.3): b64x2, two characters a value, 0-4095.
  var CMAX = 4095;
  // research.card_summary.THUMB_ALPHABET: one base64url character a value.
  var ALPHABET =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  // research.card_summary.RAMP_STEPS: a Heatmap / River cell's 32-step ramp
  // position; `.` not run, `x` failed, `_` not a valid setting, `-` no value.
  var RAMP = "0123456789abcdefghijklmnopqrstuv";
  var SEP = " · ";
  var GAP_PAIR = "..";
  // keel-app's chart stops (lib/design/tokens.ts screenMarks), so a card's
  // cell is the colour the app's cell is.
  var DIV = [
    [240, 177, 51],
    [138, 106, 42],
    [42, 53, 80],
    [59, 95, 176],
    [124, 117, 255],
  ];
  var SEQ = [
    [20, 28, 51],
    [37, 48, 122],
    [68, 64, 196],
    [111, 104, 255],
    [169, 164, 255],
    [230, 228, 255],
  ];
  var CAT = [
    "#7c75ff",
    "#25d0c5",
    "#f0b133",
    "#60a5fa",
    "#f472b6",
    "#a3e635",
    "#0da2e7",
    "#fb923c",
    "#3fcf98",
    "#b992f7",
    "#db2777",
    "#9333ea",
  ];

  // MCP-CARDS (D-420, CARD-RESEARCH RESEARCH.md §5): one validated palette per
  // host theme, by job, never cycled. Identity: the categorical slots after
  // the accent (series 1 is the subject, drawn in the accent by card-study.css);
  // polarity: binned amber to indigo through a neutral grey; magnitude: one
  // indigo. The palette is the host adapter's (`H.palette()`, shared with the
  // screen card, SCREENS-CARD); the theme is the host's (Q-7890).

  /** The palette for the host's theme, set before each draw. */
  function applyPalette() {
    var p = H.palette();
    CAT = p.cat;
    DIV = p.div;
    SEQ = p.seq;
  }

  function svg(tag, attrs) {
    var node = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] != null) node.setAttribute(k, attrs[k]);
    });
    return node;
  }

  function fail(why) {
    var e = new Error(why);
    e.thumb = true;
    throw e;
  }

  function words(text) {
    return typeof text === "string" && text ? text : "";
  }

  function arrow(label) {
    return label + " ↗";
  }

  /** `links.chart` / `links.select` carry `{v}` and `{sel}`: the card puts
   *  the served tokens in and nothing else (13 §3.3). `v` is served already
   *  URL-encoded (links.py); `sel` is encoded here. */
  function fill(template, tokens) {
    return String(template || "").replace(/\{(\w+)\}/g, function (m, k) {
      if (tokens[k] == null) return m;
      return k === "v" ? String(tokens[k]) : encodeURIComponent(tokens[k]);
    });
  }

  function linkButton(label, url, cls) {
    var a = el("a", cls || "st-link", arrow(label));
    a.href = url;
    a.title = url;
    a.addEventListener("click", function (ev) {
      ev.preventDefault();
      H.openLink(url);
    });
    return a;
  }

  // ── Thumb decoders: one per family, exactly what view_thumb_facts writes ─

  /** `enc: "b64"` values (encode_thumb_values' inverse); `.` (THUMB_GAP) is no point; throws off-alphabet. */
  function b64(text) {
    if (typeof text !== "string" || !text.length) fail("b64");
    var out = [];
    for (var i = 0; i < text.length; i++) {
      if (text.charAt(i) === ".") {
        out.push(null);
        continue;
      }
      var v = ALPHABET.indexOf(text.charAt(i));
      if (v < 0) fail("b64");
      out.push(v);
    }
    return out;
  }

  /** Comma-joined whole numbers in 0..top; an empty item is a gap (null). */
  function ints(text, top, gaps) {
    if (typeof text !== "string" || !text.length) fail("ints");
    return text.split(",").map(function (t) {
      if (t === "") {
        if (!gaps) fail("ints");
        return null;
      }
      var v = Number(t);
      if (!(v === Math.floor(v) && v >= 0 && v <= top)) fail("ints");
      return v;
    });
  }

  function lineValues(facts, text) {
    if (text == null) return null;
    return facts.enc === "b64" ? b64(text) : ints(text, TH - 1, true);
  }

  /** Lines, Curve, Fan: `{enc, series: [{c, y, lo?, hi?}], zero?, split?}`. */
  function decodeLines(f) {
    if (!f || !Array.isArray(f.series) || !f.series.length) fail("series");
    var series = f.series.map(function (s, k) {
      var y = lineValues(f, s && s.y);
      if (!y) fail("y");
      var lo = k < 3 ? lineValues(f, s.lo) : null;
      var hi = k < 3 ? lineValues(f, s.hi) : null;
      if ((lo && lo.length !== y.length) || (hi && hi.length !== y.length))
        fail("band");
      return { c: Number(s.c) || 0, y: y, lo: lo && hi ? lo : null, hi: hi };
    });
    return { kind: "lines", series: series, zero: f.zero, split: f.split };
  }

  /** Bars: `{y, lo?, hi?, zero}` comma-joined rows 0-63, empty = no value. */
  function decodeBars(f) {
    if (!f || typeof f.zero !== "number") fail("zero");
    var y = ints(f.y, TH - 1, true);
    var lo = f.lo != null ? ints(f.lo, TH - 1, true) : null;
    var hi = f.hi != null ? ints(f.hi, TH - 1, true) : null;
    if ((lo && lo.length !== y.length) || (hi && hi.length !== y.length))
      fail("whiskers");
    return { kind: "bars", y: y, lo: lo, hi: hi, zero: f.zero };
  }

  /** Heatmap: `{c, r, ramp, d, ring?}`, one character a cell, row by row. */
  function decodeHeat(f) {
    if (
      !f ||
      !(f.c > 0 && f.r > 0) ||
      (f.ramp !== "div" && f.ramp !== "seq") ||
      typeof f.d !== "string" ||
      f.d.length !== f.c * f.r
    )
      fail("heat");
    return {
      kind: "heat",
      c: f.c,
      r: f.r,
      ramp: f.ramp,
      d: f.d,
      ring: f.ring,
    };
  }

  /** Distribution: `{h, m?}`: bin heights 0-63, the marker's column 0-299. */
  function decodeDist(f) {
    if (!f) fail("dist");
    var h = ints(f.h, TH - 1, false);
    return { kind: "dist", h: h, m: typeof f.m === "number" ? f.m : null };
  }

  /** Scatter: `{x, y}`: columns 0-299 and rows 0-63, one pair a point. */
  function decodeScatter(f) {
    if (!f) fail("scatter");
    var x = ints(f.x, TW - 1, false);
    var y = ints(f.y, TH - 1, false);
    if (x.length !== y.length) fail("scatter");
    return { kind: "scatter", x: x, y: y };
  }

  /** River: `{ramp, rows: ["…"]}`: a ramp row per series. */
  function decodeRiver(f) {
    if (
      !f ||
      (f.ramp !== "div" && f.ramp !== "seq") ||
      !Array.isArray(f.rows) ||
      !f.rows.length
    )
      fail("river");
    f.rows.forEach(function (r) {
      if (typeof r !== "string" || !r.length) fail("river");
    });
    return { kind: "river", ramp: f.ramp, rows: f.rows };
  }

  /** Checks, Table: `{rows: [{name, value, met}]}`, values served as words. */
  function decodeRows(f) {
    if (!f || !Array.isArray(f.rows) || !f.rows.length) fail("rows");
    return {
      kind: "rows",
      rows: f.rows.map(function (r) {
        return { label: r.name, value_words: r.value, met: r.met };
      }),
    };
  }

  var DECODERS = {
    Lines: decodeLines,
    Curve: decodeLines,
    Fan: decodeLines,
    Bars: decodeBars,
    Heatmap: decodeHeat,
    Distribution: decodeDist,
    Scatter: decodeScatter,
    River: decodeRiver,
    Checks: decodeRows,
    Table: decodeRows,
  };

  /** The thumb as a drawable model, or null when there is none to draw. */
  function decodeThumb(thumb) {
    if (!thumb || !thumb.facts) return null;
    var decode = DECODERS[thumb.family];
    if (!decode) fail("family");
    return decode(thumb.facts);
  }

  // ── Geometry shared by thumbs and charts ────────────────────────────────

  function rowY(v) {
    return TH - 1 - v;
  }

  function colX(i, n) {
    return n > 1 ? (i * (TW - 1)) / (n - 1) : TW / 2;
  }

  // Geometry only: a coordinate to a tenth of a unit, never a number a reader
  // sees.
  function round1(v) {
    return Math.round(v * 10) / 10;
  }

  function pathOf(values, xAt, yAt) {
    var d = "";
    var open = false;
    values.forEach(function (v, i) {
      if (v == null) {
        open = false;
        return;
      }
      d += (open ? "L" : "M") + round1(xAt(i)) + " " + round1(yAt(v));
      open = true;
    });
    return d;
  }

  function bandOf(lo, hi, xAt, yAt) {
    var top = [];
    var bottom = [];
    for (var i = 0; i < lo.length; i++) {
      if (lo[i] == null || hi[i] == null) continue;
      var x = round1(xAt(i));
      top.push(x + " " + round1(yAt(hi[i])));
      bottom.unshift(x + " " + round1(yAt(lo[i])));
    }
    return top.length > 1
      ? "M" + top.join("L") + "L" + bottom.join("L") + "Z"
      : "";
  }

  function colourOf(c) {
    return c > 0 ? CAT[(c - 1) % CAT.length] : null;
  }

  function lerp(stops, t) {
    t = Math.max(0, Math.min(1, t));
    var n = stops.length - 1;
    var i = Math.min(n - 1, Math.floor(t * n));
    var f = t * n - i;
    var a = stops[i];
    var b = stops[i + 1];
    return (
      "rgb(" +
      [0, 1, 2]
        .map(function (k) {
          return Math.round(a[k] + (b[k] - a[k]) * f);
        })
        .join(",") +
      ")"
    );
  }

  function stepColour(ch, ramp) {
    var k = RAMP.indexOf(ch);
    if (k < 0) return null;
    var t = k / (RAMP.length - 1);
    return ramp === "div" ? lerp(DIV, t) : lerp(SEQ, 0.2 + 0.8 * t);
  }

  function rampGradient(ramp) {
    var stops = [];
    for (var k = 0; k <= 4; k++)
      stops.push(stepColour(RAMP.charAt(Math.round((k * 31) / 4)), ramp));
    return "linear-gradient(90deg," + stops.join(",") + ")";
  }

  function hline(y, cls, w) {
    return svg("line", {
      x1: 0,
      x2: w,
      y1: y,
      y2: y,
      class: cls,
      "vector-effect": "non-scaling-stroke",
    });
  }

  function vline(x, cls, h) {
    return svg("line", {
      x1: x,
      x2: x,
      y1: 0,
      y2: h,
      class: cls,
      "vector-effect": "non-scaling-stroke",
    });
  }

  function dot(x, y, cls) {
    // A zero-length round-capped stroke is a dot whatever the box's aspect.
    return svg("path", {
      d: "M" + round1(x) + " " + round1(y) + "h0",
      class: cls,
      "vector-effect": "non-scaling-stroke",
    });
  }

  // ── Thumb drawing (300 x 64, scaled into its box) ──────────────────────

  function drawThumbLines(box, m) {
    var xAt = function (n) {
      return function (i) {
        return colX(i, n);
      };
    };
    if (m.zero != null) box.appendChild(hline(rowY(m.zero), "st-zero", TW));
    m.series.forEach(function (s) {
      if (!s.lo) return;
      var d = bandOf(s.lo, s.hi, xAt(s.lo.length), rowY);
      if (d)
        box.appendChild(
          svg("path", { d: d, class: "st-band", fill: colourOf(s.c) }),
        );
    });
    m.series.forEach(function (s) {
      box.appendChild(
        svg("path", {
          d: pathOf(s.y, xAt(s.y.length), rowY),
          class: "st-line" + (s.c < 0 ? " st-ref" : ""),
          stroke: colourOf(s.c),
          "vector-effect": "non-scaling-stroke",
        }),
      );
    });
    if (m.split != null) box.appendChild(vline(m.split, "st-split", TH));
  }

  function drawThumbBars(box, m) {
    var w = TW / m.y.length;
    var gap = Math.min(4, w * 0.2);
    var zero = rowY(m.zero);
    m.y.forEach(function (v, i) {
      if (v == null) return;
      box.appendChild(
        svg("rect", {
          x: round1(i * w + gap / 2),
          y: Math.min(zero, rowY(v)),
          width: round1(Math.max(0.5, w - gap)),
          height: Math.max(0.5, Math.abs(rowY(v) - zero)),
          class: "st-bar",
        }),
      );
      if (m.lo && m.hi && m.lo[i] != null && m.hi[i] != null) {
        var x = round1(i * w + w / 2);
        box.appendChild(
          svg("line", {
            x1: x,
            x2: x,
            y1: rowY(m.hi[i]),
            y2: rowY(m.lo[i]),
            class: "st-whisker",
            "vector-effect": "non-scaling-stroke",
          }),
        );
      }
    });
    box.appendChild(hline(zero, "st-zero", TW));
  }

  function cells(box, chars, cols, rows, ramp, opts) {
    var o = opts || {};
    var W = o.w || TW;
    var Hh = o.h || TH;
    var cw = W / cols;
    var ch = Hh / rows;
    // A served CardChart heatmap's row 0 is its y axis's first value, drawn
    // at the bottom as its tick says (13 §3.3); a thumb's row 0 is the top.
    var top = function (i) {
      var r = Math.floor(i / cols);
      return (o.flip ? rows - 1 - r : r) * ch;
    };
    for (var i = 0; i < chars.length; i++) {
      var k = chars.charAt(i);
      var attrs = {
        x: (i % cols) * cw,
        y: top(i),
        width: cw,
        height: ch,
      };
      var colour = stepColour(k, ramp);
      if (colour) {
        attrs.fill = colour;
        attrs.class = "st-cell";
      } else if (k === ".") {
        attrs.class = "st-cell st-cell-notrun";
      } else if (k === "x") {
        attrs.class = "st-cell st-cell-failed";
        attrs["vector-effect"] = "non-scaling-stroke";
      } else {
        continue; // `-` no value, `_` not a valid setting: blank
      }
      box.appendChild(svg("rect", attrs));
    }
    var mark = function (i, cls) {
      if (typeof i !== "number" || i < 0 || i >= chars.length) return;
      box.appendChild(
        svg("rect", {
          x: (i % cols) * cw,
          y: top(i),
          width: cw,
          height: ch,
          class: cls,
          "vector-effect": "non-scaling-stroke",
        }),
      );
    };
    ((o.outline && o.outline.high) || []).forEach(function (i) {
      mark(i, "st-outline st-outline-high");
    });
    ((o.outline && o.outline.low) || []).forEach(function (i) {
      mark(i, "st-outline st-outline-low");
    });
    mark(o.ring, "st-ring");
  }

  function drawThumbHeat(box, m) {
    // The thumb's row 0 is the y axis's first value (card_summary's
    // `_heatmap_facts`), drawn at the bottom as the study's own map draws it
    // (CARD-RENDER share-read: the thumb was the map upside down).
    cells(box, m.d, m.c, m.r, m.ramp, { ring: m.ring, flip: true });
  }

  function riverChars(rows) {
    var cols = 0;
    rows.forEach(function (r) {
      cols = Math.max(cols, r.length);
    });
    return {
      cols: cols,
      d: rows
        .map(function (r) {
          while (r.length < cols) r += "-";
          return r;
        })
        .join(""),
    };
  }

  function drawThumbRiver(box, m) {
    var r = riverChars(m.rows);
    cells(box, r.d, r.cols, m.rows.length, m.ramp, {});
  }

  function drawThumbDist(box, m) {
    var w = TW / m.h.length;
    m.h.forEach(function (v, i) {
      box.appendChild(
        svg("rect", {
          x: round1(i * w + 0.5),
          y: rowY(v),
          width: round1(Math.max(0.5, w - 1)),
          height: v + 1,
          class: "st-bin",
        }),
      );
    });
    if (m.m != null) box.appendChild(vline(m.m, "st-marker", TH));
  }

  function drawThumbScatter(box, m) {
    m.x.forEach(function (x, i) {
      box.appendChild(dot(x + 0.5, rowY(m.y[i]) + 0.5, "st-dot"));
    });
  }

  var DRAW_THUMB = {
    lines: drawThumbLines,
    bars: drawThumbBars,
    heat: drawThumbHeat,
    river: drawThumbRiver,
    dist: drawThumbDist,
    scatter: drawThumbScatter,
  };

  function thumbSvg(model) {
    var box = svg("svg", {
      viewBox: "0 0 " + TW + " " + TH,
      preserveAspectRatio: "none",
      class: "st-plot",
      "aria-hidden": "true",
      focusable: "false",
    });
    DRAW_THUMB[model.kind](box, model);
    return box;
  }

  /** The top rows with their served words (13 §2.2's last fallback). */
  function rowsOf(rows, max) {
    var list = el("ul", "st-rows");
    rows.slice(0, max || MAX_ROWS).forEach(function (row) {
      var item = el("li", "st-row");
      if (row.met === true || row.met === false)
        item.setAttribute("data-met", String(row.met));
      var label = el("span", "st-row-label", row.label || "");
      if (row.label) label.title = row.label;
      item.appendChild(label);
      if (row.bar_words)
        item.appendChild(el("span", "st-row-bar", row.bar_words));
      // No served words → the em dash (card-study.css), never the raw value.
      item.appendChild(el("span", "st-row-value", row.value_words || ""));
      list.appendChild(item);
    });
    return list;
  }

  /** A thumb drawn in `box`, else the rows; false when nothing drew. */
  function drawThumbInto(box, thumb, fallbackRows, slot) {
    var model = null;
    try {
      model = decodeThumb(thumb);
    } catch (e) {
      if (!e.thumb) throw e;
      // A served thumb this decoder cannot read is a contract break: say so
      // where a developer looks, and draw the served rows rather than guess.
      (slot || box).setAttribute("data-thumb-error", e.message);
      if (window.console && console.error)
        console.error("keel study card: undecodable thumb", thumb);
    }
    if (model && model.kind === "rows") {
      box.appendChild(rowsOf(model.rows));
      return true;
    }
    if (model) {
      var wrap = el("div", "st-picture");
      wrap.setAttribute("data-family", thumb.family);
      wrap.setAttribute("data-view", String(thumb.view_index));
      wrap.appendChild(thumbSvg(model));
      box.appendChild(wrap);
      return true;
    }
    if (fallbackRows && fallbackRows.length) {
      box.appendChild(rowsOf(fallbackRows));
      return true;
    }
    return false;
  }

  // ── CardChart (13 §3.3): decode and draw at card resolution ────────────

  /** b64x2: two base64url characters a value 0-4095; ".." is a gap. */
  function b64x2(text) {
    if (text == null) return null;
    if (typeof text !== "string" || text.length % 2) fail("b64x2");
    var out = [];
    for (var i = 0; i < text.length; i += 2) {
      var pair = text.substr(i, 2);
      if (pair === GAP_PAIR) {
        out.push(null);
        continue;
      }
      var hi = ALPHABET.indexOf(pair.charAt(0));
      var lo = ALPHABET.indexOf(pair.charAt(1));
      if (hi < 0 || lo < 0) fail("b64x2");
      out.push(hi * 64 + lo);
    }
    return out;
  }

  function split(text) {
    return typeof text === "string" && text.length ? text.split("|") : [];
  }

  /** The x position (0-4095) of each of `n` points: served `pos`, else even. */
  function xPositions(axis, n) {
    var pos = axis && axis.pos ? b64x2(axis.pos) : null;
    if (pos && pos.length === n) return pos;
    var out = [];
    for (var i = 0; i < n; i++)
      out.push(n > 1 ? (i * CMAX) / (n - 1) : CMAX / 2);
    return out;
  }

  function yOf(v) {
    return CMAX - v;
  }

  function chartSvg() {
    return svg("svg", {
      viewBox: "0 0 " + CMAX + " " + CMAX,
      preserveAspectRatio: "none",
      class: "st-plot",
      "aria-hidden": "true",
      focusable: "false",
    });
  }

  /** Decode a CardChart into a drawable model; throws on a broken one. */
  function decodeChart(c) {
    if (!c || typeof c !== "object" || !c.kind) fail("chart");
    var m = { kind: c.kind, c: c };
    if (c.kind === "line") {
      m.series = (c.series || []).map(function (s) {
        return {
          name: s.name,
          c: Number(s.c) || 0,
          ref: !!s.ref || Number(s.c) < 0,
          y: b64x2(s.y) || [],
          lo: b64x2(s.lo),
          hi: b64x2(s.hi),
        };
      });
      if (!m.series.length || !m.series[0].y.length) fail("series");
      m.xs = xPositions(c.x, m.series[0].y.length);
    } else if (c.kind === "bars") {
      var s0 = (c.series || [])[0] || {};
      m.y = b64x2(s0.y) || [];
      m.lo = b64x2(s0.lo);
      m.hi = b64x2(s0.hi);
      m.zero = typeof c.zero === "number" ? c.zero : 0;
      if (!m.y.length) fail("bars");
    } else if (c.kind === "heat") {
      if (
        !(c.cols > 0 && c.rows > 0) ||
        typeof c.d !== "string" ||
        c.d.length !== c.cols * c.rows
      )
        fail("heat");
    } else if (c.kind === "dist") {
      m.bins = b64x2(c.bins) || [];
      if (!m.bins.length) fail("dist");
    } else if (c.kind === "scatter") {
      var p = b64x2(c.pts) || [];
      if (!p.length || p.length % 2) fail("scatter");
      m.pts = p;
    } else if (c.kind === "river") {
      if (!Array.isArray(c.rows) || !c.rows.length) fail("river");
    } else if (c.kind === "table") {
      if (!Array.isArray(c.columns) || !Array.isArray(c.rows)) fail("table");
    } else if (c.kind === "checks") {
      if (!Array.isArray(c.rows)) fail("checks");
    } else {
      fail("kind");
    }
    return m;
  }

  function drawLineChart(box, m, isolated) {
    var c = m.c;
    (c.marks || []).forEach(function (mk) {
      if (mk.kind === "hline")
        box.appendChild(hline(yOf(mk.at), "st-zero", CMAX));
      else if (mk.kind === "vline") {
        // MCP-CARDS (RESEARCH.md §4 Check): the days after the split, tinted,
        // so "does it hold after the split" reads without a legend.
        box.appendChild(
          svg("rect", {
            x: mk.at,
            y: 0,
            width: Math.max(0, CMAX - mk.at),
            height: CMAX,
            class: "st-after",
          }),
        );
        box.appendChild(vline(mk.at, "st-split", CMAX));
      }
    });
    var xAt = function (i) {
      return m.xs[i];
    };
    // AUDIT-CHARTS F55/F66 (FIX-11): "your setting" on a line over a setting
    // axis, a ring on the first drawn series at the served x.
    (c.marks || []).forEach(function (mk) {
      if (mk.kind !== "point") return;
      var s = m.series.filter(function (x) {
        return !x.ref;
      })[0];
      // the drawn x nearest the served one (even positions are fractional)
      var i = -1;
      m.xs.forEach(function (x, j) {
        if (
          Math.abs(x - mk.at) <= 2 &&
          (i < 0 || Math.abs(x - mk.at) < Math.abs(m.xs[i] - mk.at))
        )
          i = j;
      });
      if (!s || i < 0 || s.y[i] == null) {
        box.appendChild(vline(mk.at, "st-split", CMAX));
        return;
      }
      box.appendChild(dot(m.xs[i], yOf(s.y[i]), "st-yours-out"));
      box.appendChild(dot(m.xs[i], yOf(s.y[i]), "st-yours-in"));
    });
    m.series.forEach(function (s, k) {
      if (!s.lo || !s.hi || k >= 3) return;
      var d = bandOf(s.lo, s.hi, xAt, yOf);
      if (d)
        box.appendChild(
          svg("path", { d: d, class: "st-band", fill: colourOf(s.c) }),
        );
    });
    var withheld = (c.read && c.read.withheld) || null;
    m.series.forEach(function (s, k) {
      box.appendChild(
        svg("path", {
          d: pathOf(s.y, xAt, yOf),
          class:
            "st-line" +
            (s.ref ? " st-ref" : "") +
            (isolated != null && isolated !== k ? " st-dim" : ""),
          stroke: colourOf(s.c),
          "data-series": k,
          "vector-effect": "non-scaling-stroke",
        }),
      );
      // AUDIT-CHARTS F55 (FIX-11): a point with no neighbour drew nothing (a
      // path of one "M" is no ink), so a 1-axis map's key read as axes only.
      // A short series dots every value; a long one dots its isolated ones.
      var few = s.y.length <= 40;
      s.y.forEach(function (v, i) {
        if (v == null) return;
        var alone =
          (i === 0 || s.y[i - 1] == null) &&
          (i === s.y.length - 1 || s.y[i + 1] == null);
        if (!few && !alone) return;
        var d = dot(m.xs[i], yOf(v), "st-dot" + (s.ref ? " st-ref" : ""));
        if (s.c > 0) d.setAttribute("stroke", colourOf(s.c));
        d.setAttribute("data-series", k);
        box.appendChild(d);
      });
      // Withheld points (13 §2.5 Curve): hollow, never a value.
      if (withheld)
        Object.keys(withheld).forEach(function (i) {
          var v = s.y[Number(i)];
          if (v != null)
            box.appendChild(dot(m.xs[Number(i)], yOf(v), "st-hollow"));
        });
    });
  }

  function drawBarsChart(box, m) {
    var w = CMAX / m.y.length;
    var gap = Math.min(w * 0.25, 120);
    // CARD-RENDER compare-done: one bar filled the whole plot; a bar is at
    // most a sixth of it, centred on its place.
    var bw = Math.max(1, Math.min(w - gap, CMAX / 6));
    var zero = yOf(m.zero);
    m.y.forEach(function (v, i) {
      if (v == null) return;
      box.appendChild(
        svg("rect", {
          x: round1(i * w + (w - bw) / 2),
          y: Math.min(zero, yOf(v)),
          width: round1(bw),
          height: Math.max(8, Math.abs(yOf(v) - zero)),
          class: "st-bar",
        }),
      );
      if (m.lo && m.hi && m.lo[i] != null && m.hi[i] != null) {
        var x = round1(i * w + w / 2);
        box.appendChild(
          svg("line", {
            x1: x,
            x2: x,
            y1: yOf(m.hi[i]),
            y2: yOf(m.lo[i]),
            class: "st-whisker",
            "vector-effect": "non-scaling-stroke",
          }),
        );
      }
    });
    box.appendChild(hline(zero, "st-zero", CMAX));
  }

  function drawDistChart(box, m) {
    var w = CMAX / m.bins.length;
    m.bins.forEach(function (v, i) {
      if (v == null) return;
      box.appendChild(
        svg("rect", {
          x: round1(i * w + w * 0.04),
          y: yOf(v),
          width: round1(w * 0.92),
          height: Math.max(8, v),
          class: "st-bin",
        }),
      );
    });
    (m.c.overlays || []).forEach(function (ov, k) {
      var hs = ov && ov.bins ? b64x2(ov.bins) : null;
      if (!hs) return;
      box.appendChild(
        svg("path", {
          d: pathOf(
            hs,
            function (i) {
              return (i + 0.5) * (CMAX / hs.length);
            },
            yOf,
          ),
          class: "st-line st-overlay",
          stroke: CAT[(k + 1) % CAT.length],
          "vector-effect": "non-scaling-stroke",
        }),
      );
    });
    if (m.c.marker && typeof m.c.marker.at === "number")
      box.appendChild(vline(m.c.marker.at, "st-marker", CMAX));
  }

  function drawScatterChart(box, m) {
    // `pts`: each point's x then its y, b64x2; `cat` one base-36 character a
    // point, the index of its served `cats` entry.
    var cats = m.c.cat || "";
    for (var i = 0; i + 1 < m.pts.length; i += 2) {
      if (m.pts[i] == null || m.pts[i + 1] == null) continue;
      var p = dot(m.pts[i], yOf(m.pts[i + 1]), "st-dot");
      var k = cats ? parseInt(cats.charAt(i / 2), 36) : NaN;
      if (!isNaN(k)) p.setAttribute("stroke", CAT[k % CAT.length]);
      box.appendChild(p);
    }
  }

  function drawHeatChart(box, m) {
    var c = m.c;
    // "Your setting" is a true ring (ringOverlay), never the square outline
    // Highest wears (CARD-RENDER map-done).
    cells(box, c.d, c.cols, c.rows, (c.legend && c.legend.ramp) || "div", {
      w: CMAX,
      h: CMAX,
      outline: c.outline,
      flip: true,
    });
  }

  /** A heatmap's "your setting": a round ring centred on its cell. */
  function ringOverlay(plot, c) {
    if (typeof c.ring !== "number" || c.ring < 0 || c.ring >= c.cols * c.rows)
      return;
    var col = c.ring % c.cols;
    var row = Math.floor(c.ring / c.cols);
    var dot = el("span", "st-ring-dot");
    dot.style.left = ((col + 0.5) / c.cols) * 100 + "%";
    dot.style.top = ((c.rows - 1 - row + 0.5) / c.rows) * 100 + "%";
    dot.setAttribute("aria-hidden", "true");
    plot.appendChild(dot);
    // Round, around the cell's written value: as wide as the cell allows.
    var size = function () {
      var w = plot.clientWidth / c.cols;
      var h = plot.clientHeight / c.rows;
      if (!w || !h) return;
      var d = Math.max(10, Math.min(w, h, 44) - 4);
      dot.style.width = dot.style.height = d + "px";
      dot.style.margin = -d / 2 + "px 0 0 " + -d / 2 + "px";
    };
    size();
    requestAnimationFrame(size);
  }

  function drawRiverChart(box, m) {
    var c = m.c;
    var r = riverChars(c.rows);
    cells(
      box,
      r.d,
      r.cols,
      c.rows.length,
      (c.legend && c.legend.ramp) || "seq",
      { w: CMAX, h: CMAX },
    );
  }

  var DRAW_CHART = {
    line: drawLineChart,
    bars: drawBarsChart,
    dist: drawDistChart,
    scatter: drawScatterChart,
    heat: drawHeatChart,
    river: drawRiverChart,
  };

  /** Axis tick words at their served positions (0-4095 along the axis). */
  function ticksOf(axis, dir) {
    var box = el("div", "st-ticks st-ticks-" + dir);
    ((axis && axis.ticks) || []).forEach(function (t) {
      if (!t || typeof t.at !== "number" || !t.w) return;
      var s = el("span", "st-tick", t.w);
      var at = (t.at / CMAX) * 100;
      if (dir === "x") {
        s.style.left = at + "%";
        if (t.at <= CMAX * 0.05) s.className += " st-tick-first";
        else if (t.at >= CMAX * 0.95) s.className += " st-tick-last";
      } else {
        s.style.bottom = at + "%";
        if (t.at <= CMAX * 0.05) s.className += " st-tick-first";
        else if (t.at >= CMAX * 0.95) s.className += " st-tick-last";
      }
      box.appendChild(s);
    });
    return box;
  }

  /**
   * After layout: the y tick column takes the width its words need (up to a
   * third of the chart, an ellipsis past that), and an x tick that would
   * touch the one before it is hidden, never squeezed (the last is kept).
   */
  function fitTicks(block, yTicks, xTicks) {
    var total = block.clientWidth;
    if (!total) return;
    var widest = 0;
    var ys = yTicks.querySelectorAll(".st-tick");
    for (var i = 0; i < ys.length; i++)
      widest = Math.max(widest, ys[i].scrollWidth);
    var w = Math.min(Math.ceil(widest) + 2, Math.floor(total / 3));
    yTicks.style.width = (w > 0 ? w : 0) + "px";
    // The x ticks start where the plot does (a map's y axis name sits left of
    // its ticks, D-352 (3)).
    var plotEl = yTicks.nextElementSibling;
    xTicks.style.marginLeft = plotEl
      ? Math.max(
          0,
          Math.round(
            plotEl.getBoundingClientRect().left -
              block.getBoundingClientRect().left,
          ),
        ) + "px"
      : "0px";
    var xs = xTicks.querySelectorAll(".st-tick");
    var last = null;
    var rects = [];
    var box = xTicks.getBoundingClientRect();
    var outer = block.getBoundingClientRect();
    for (var j = 0; j < xs.length; j++) {
      xs[j].removeAttribute("data-hidden");
      // Keep every word inside the chart's width: a centred word near an
      // edge slides in rather than being cut.
      var rr = xs[j].getBoundingClientRect();
      var shift =
        rr.right > box.right
          ? box.right - rr.right
          : rr.left < outer.left
            ? outer.left - rr.left
            : 0;
      if (shift) xs[j].style.marginLeft = shift + "px";
      rects.push(xs[j].getBoundingClientRect());
    }
    for (var k = 0; k < xs.length; k++) {
      var keepLast = k === xs.length - 1;
      if (last !== null && rects[k].left < rects[last].right + 6) {
        if (keepLast) xs[last].setAttribute("data-hidden", "1");
        else {
          xs[k].setAttribute("data-hidden", "1");
          continue;
        }
      }
      last = k;
    }
  }

  /** A Heatmap's colour legend: the ramp between its served words. */
  /**
   * D-352 (3): one line under a map: what its outlined cells mean, each a
   * swatch drawn like the cell and its served word, and the x axis's name.
   */
  function heatKey(chart, m, copy) {
    var row = el("div", "st-heat-key");
    var items = [
      [((chart.outline || {}).high || []).length, "high", copy.highest],
      [((chart.outline || {}).low || []).length, "low", copy.lowest],
      // The ringed cell is named in its served words ("Your setting: …").
      [
        chart.ring != null && chart.ring !== "",
        "ring",
        (chart.read && chart.read.ring_words) || copy.yours,
      ],
    ];
    items.forEach(function (it) {
      if (!it[0] || !it[2]) return;
      var k = el("span", "st-heat-key-item");
      k.appendChild(el("span", "st-hk-swatch st-hk-swatch-" + it[1]));
      k.appendChild(el("span", "", it[2]));
      row.appendChild(k);
    });
    var xName = chart.x && chart.x.label;
    if (xName) {
      var xn = el("span", "st-axis-name st-axis-name-x", words(xName));
      xn.title = xName;
      row.appendChild(xn);
    }
    return row.childNodes.length ? row : null;
  }

  function rampLegend(c) {
    var lg = c.legend;
    if (!lg || !lg.lo || !lg.hi) return null;
    var box = el("span", "st-legend st-ramp-legend");
    // What the colour measures, served (CARD-RENDER map-done).
    if (lg.label) {
      var nm = el("span", "st-legend-w st-legend-name", lg.label);
      nm.title = lg.label;
      box.appendChild(nm);
    }
    box.appendChild(el("span", "st-legend-w", lg.lo));
    var bar = el("span", "st-ramp");
    bar.style.background = rampGradient(lg.ramp || "div");
    box.appendChild(bar);
    box.appendChild(el("span", "st-legend-w", lg.hi));
    return box;
  }

  /** The series keys of a chart with >= 2 named series: tap isolates one. */
  function seriesLegend(m, state, onIsolate) {
    var names = [];
    if (m.kind === "line")
      m.series.forEach(function (s, k) {
        if (s.name) names.push({ k: k, name: s.name, c: s.c, ref: s.ref });
      });
    if (m.kind === "scatter")
      (m.c.cats || []).forEach(function (cat) {
        if (cat && cat.w)
          names.push({ k: null, name: cat.w, c: (Number(cat.c) || 0) + 1 });
      });
    if (names.length < 2) return null;
    var box = el("span", "st-legend");
    names.forEach(function (n) {
      var b = el(
        n.k == null ? "span" : "button",
        "st-key" + (n.ref ? " st-key-ref" : ""),
      );
      var sw = el("i", "st-swatch");
      var colour = colourOf(n.c);
      if (colour) sw.style.background = colour;
      b.appendChild(sw);
      b.appendChild(document.createTextNode(n.name));
      b.title = n.name;
      if (n.k != null) {
        b.type = "button";
        b.setAttribute("data-series", String(n.k));
        b.setAttribute(
          "aria-pressed",
          state.isolated === n.k ? "true" : "false",
        );
        b.addEventListener("click", function (ev) {
          ev.stopPropagation();
          onIsolate(state.isolated === n.k ? null : n.k);
        });
      }
      box.appendChild(b);
    });
    return box;
  }

  /** The words a readout shows for index `i` (13 §4.1), served only. */
  /** MCP-CARDS (integrator polish 6): on the face a one-series line chart's
   *  readout leads with its value, the date quiet after it ("0.86× · Oct 28,
   *  2026"); anything else reads as readWords does. */
  function faceRead(m, i) {
    var r = m.c.read || {};
    if (m.kind === "line" && (r.y || []).length === 1 && i != null) {
      var v = split(r.y[0])[i];
      var band = split((r.band || [])[0])[i];
      if (v)
        return {
          bold: v + (band ? " (" + band + ")" : ""),
          rest: split(r.x)[i] || "",
        };
    }
    return { bold: "", rest: i == null ? "" : readWords(m, i) };
  }

  function readWords(m, i) {
    var r = m.c.read || {};
    if (m.kind === "heat") {
      var c = m.c;
      var at = [];
      var col = split(r.x)[i % c.cols];
      var row = split((r.y || [])[0])[Math.floor(i / c.cols)];
      if (col)
        at.push(
          ((c.x && c.x.label) || "") + (c.x && c.x.label ? " " : "") + col,
        );
      if (row)
        at.push(
          ((c.y && c.y.label) || "") + (c.y && c.y.label ? " " : "") + row,
        );
      var cell = split(r.cells)[i];
      if (cell) at.push(cell);
      return at.join(SEP);
    }
    if (m.kind === "dist") return split(r.bins)[i] || "";
    var parts = [];
    var xw = split(r.x)[i];
    if (xw) parts.push(xw);
    var bands = r.band || [];
    (r.y || []).forEach(function (row, k) {
      var w = split(row)[i];
      if (!w) return;
      // The series by name, else (one unnamed series) the measure itself, so
      // the readout says what the number is (CARD-RENDER #2).
      var name =
        m.series && m.series.length > 1 && m.series[k]
          ? m.series[k].name
          : (m.series && m.series[k] && m.series[k].name) ||
            (m.c.y && m.c.y.label) ||
            null;
      var band = split(bands[k])[i];
      parts.push(
        (name ? name + " " : "") + w + (band ? " (" + band + ")" : ""),
      );
    });
    var withheld = r.withheld && r.withheld[String(i)];
    if (withheld) parts.push(withheld);
    return parts.join(SEP);
  }

  /** The select token of index `i` (13 §4.1): a bar's `read.sel[i]`; a
   *  heatmap cell's column half and row half joined (card_charts' `x:v;y:v`). */
  function selOf(m, i) {
    var r = m.c.read || {};
    if (m.kind === "bars") return split(r.sel)[i] || null;
    if (m.kind === "heat") {
      var col = split(r.sel)[i % m.c.cols];
      var row = split(r.sel_rows)[Math.floor(i / m.c.cols)];
      return col && row ? col + ";" + row : null;
    }
    return null;
  }

  function countOf(m) {
    if (m.kind === "line") return m.series[0].y.length;
    if (m.kind === "bars") return m.y.length;
    if (m.kind === "dist") return m.bins.length;
    if (m.kind === "heat") return m.c.cols * m.c.rows;
    return 0;
  }

  /** The index under a pointer at fraction (fx, fy) of the plot. */
  function indexAt(m, fx, fy) {
    var n = countOf(m);
    if (!n) return null;
    if (m.kind === "line") {
      var x = fx * CMAX;
      var best = 0;
      for (var i = 1; i < m.xs.length; i++)
        if (Math.abs(m.xs[i] - x) < Math.abs(m.xs[best] - x)) best = i;
      return best;
    }
    if (m.kind === "heat") {
      var col = Math.min(m.c.cols - 1, Math.max(0, Math.floor(fx * m.c.cols)));
      var row = Math.min(m.c.rows - 1, Math.max(0, Math.floor(fy * m.c.rows)));
      return (m.c.rows - 1 - row) * m.c.cols + col;
    }
    return Math.min(n - 1, Math.max(0, Math.floor(fx * n)));
  }

  /** The readout's crosshair, cell outline or column for index `i`. */
  function cursorOf(m, i) {
    var g = svg("g", { class: "st-cursor" });
    if (i == null) return g;
    if (m.kind === "line") {
      var x = m.xs[i];
      g.appendChild(vline(x, "st-cross", CMAX));
      m.series.forEach(function (s) {
        if (s.y[i] != null) g.appendChild(dot(x, yOf(s.y[i]), "st-cursor-dot"));
      });
    } else if (m.kind === "heat") {
      var cw = CMAX / m.c.cols;
      var ch = CMAX / m.c.rows;
      g.appendChild(
        svg("rect", {
          x: (i % m.c.cols) * cw,
          y: (m.c.rows - 1 - Math.floor(i / m.c.cols)) * ch,
          width: cw,
          height: ch,
          class: "st-cursor-cell",
          "vector-effect": "non-scaling-stroke",
        }),
      );
    } else {
      var w = CMAX / countOf(m);
      g.appendChild(
        svg("rect", {
          x: i * w,
          y: 0,
          width: w,
          height: CMAX,
          class: "st-cursor-col",
        }),
      );
    }
    return g;
  }

  /** Words written in a Heatmap's cells when each is >= 34 x 15 px (13 §2.5)
   *  and every value fits its cell whole. */
  function cellValues(plot, m) {
    var c = m.c;
    if (!c.values_in_cells) return;
    var w = plot.clientWidth;
    var h = plot.clientHeight;
    if (!w || !h || w / c.cols < 34 || h / c.rows < 15) return;
    var cellWords = split((c.read || {}).cells);
    var layer = el("div", "st-cellwords");
    layer.style.gridTemplateColumns = "repeat(" + c.cols + ", 1fr)";
    layer.style.gridTemplateRows = "repeat(" + c.rows + ", 1fr)";
    // Top row first in the grid: the last served row (row 0 is the bottom).
    // A cell with no value (not run, failed, not valid) stays blank: its words
    // are in the readout.
    for (var r = c.rows - 1; r >= 0; r--)
      for (var k = 0; k < c.cols; k++) {
        var i = r * c.cols + k;
        var valued = RAMP.indexOf(c.d.charAt(i)) >= 0;
        layer.appendChild(el("span", "", valued ? cellWords[i] || "" : ""));
      }
    plot.appendChild(layer);
    // CARD-360: a value that does not fit its cell is never half-drawn
    // ("0.…" at 360 px). The size gate above is a floor, not a proof: if
    // any value overflows its cell, the whole layer goes, so the map reads
    // as colour alone and every value stays in the readout.
    var spans = layer.children;
    for (var j = 0; j < spans.length; j++) {
      var s = spans[j];
      if (
        s.textContent &&
        (s.scrollWidth > s.clientWidth + 1 ||
          s.scrollHeight > s.clientHeight + 1)
      ) {
        plot.removeChild(layer);
        return;
      }
    }
  }

  function tableOf(c) {
    var t = el("table", "st-table");
    var headRow = el("tr");
    (c.columns || []).forEach(function (col) {
      headRow.appendChild(el("th", "", words(col && col.w)));
    });
    t.appendChild(headRow);
    (c.rows || []).slice(0, 10).forEach(function (row) {
      var tr = el("tr");
      (row || []).forEach(function (cell) {
        tr.appendChild(el("td", "", words(cell)));
      });
      t.appendChild(tr);
    });
    return t;
  }

  function checksEntry(data) {
    var found = null;
    (data.charts || []).forEach(function (e) {
      if (
        !found &&
        e &&
        (e.family === "Checks" || (e.chart && e.chart.kind === "checks"))
      )
        found = e;
    });
    return found;
  }

  /**
   * D-407 (2): a Check with no bars set (Keel's showcase, a Check before its
   * bars are set): every bar is unset, so nothing counts (served count 0) and
   * no unlocked row carries a met mark. Its rows are its measured checks,
   * with no met words or glyph; the block's served more_words is its one line.
   */
  function noBars(data) {
    var c = data && data.checks;
    return (
      !!c &&
      c.count === 0 &&
      (c.rows || []).every(function (r) {
        return r.locked || (r.met == null && !r.bar_words);
      })
    );
  }

  /** A check row's state: met, not met, not measured, locked, or "plain" (no bar set, D-407 (2)). */
  function checkState(r, plain) {
    return r.locked
      ? "locked"
      : r.met === true
        ? "met"
        : r.met === false
          ? "not_met"
          : plain
            ? "plain"
            : "not_measured";
  }

  /** "Your checks" rows (13 §2.3): met words, label, value, the rest, link. */
  function checksList(rows, data, max) {
    var copy = data.copy || {};
    var links = data.links || {};
    var view = checksEntry(data);
    var plain = noBars(data);
    var list = el("ul", "st-checks");
    (rows || []).slice(0, max || MAX_CHECKS).forEach(function (r) {
      var li = el("li", "st-check");
      var state = checkState(r, plain);
      li.setAttribute("data-state", state);
      var top = el("div", "st-check-head");
      if (state !== "plain")
        top.appendChild(el("span", "st-check-met", words(copy[state])));
      top.appendChild(el("span", "st-check-label", words(r.label)));
      // A locked row draws its words and no number (13 §2.3).
      if (!r.locked)
        top.appendChild(el("span", "st-check-value", words(r.value_words)));
      li.appendChild(top);
      var bits = r.locked
        ? []
        : [r.interval_words, r.sample_words, r.bar_words].filter(function (w) {
            return typeof w === "string" && w;
          });
      if (bits.length)
        li.appendChild(el("div", "st-check-sub", bits.join(SEP)));
      if (r.sel && links.select && view && copy.open_bar)
        li.appendChild(
          linkButton(
            copy.open_bar,
            fill(links.select, { v: view.v, sel: r.sel }),
            "st-link st-link-small",
          ),
        );
      list.appendChild(li);
    });
    return list;
  }

  /**
   * One CardChart at card resolution: its title (with the series keys or
   * the colour legend), the plot between its y and x tick words, and the
   * readout line whose idle text is the served note or hint. Hover, tap
   * and the arrow keys move the readout (13 §4.1).
   */
  function chartBlock(chart, data, opts) {
    var o = opts || {};
    var m = decodeChart(chart);
    var copy = data.copy || {};
    var state = o.state || {};
    var block = el("div", "st-chart st-chart-" + m.kind);
    block.setAttribute("data-kind", m.kind);
    var titleRow = el("div", "st-chart-title");
    // D-354 (1): a single-chart card whose chart title is the card's title
    // does not say it twice.
    var shownTitle = o.hideTitle === chart.title ? "" : chart.title;
    var name = el("span", "st-chart-name", words(shownTitle));
    if (shownTitle) name.title = shownTitle;
    titleRow.appendChild(name);
    var redraw = function () {};
    var isolate = function (k) {
      state.isolated = k;
      if (o.onState) o.onState(state);
      redraw();
    };
    var keys = seriesLegend(m, state, isolate) || rampLegend(chart);
    if (keys) titleRow.appendChild(keys);
    block.appendChild(titleRow);

    if (m.kind === "table" || m.kind === "checks") {
      // A served list with no rows draws its title and served words only, never a bare header (D-337 (1)).
      if ((chart.rows || []).length) {
        var listBox = el("div", "st-chart-body st-chart-list");
        listBox.appendChild(
          m.kind === "table" ? tableOf(chart) : checksList(chart.rows, data, 6),
        );
        block.appendChild(listBox);
      }
      block.appendChild(
        el("div", "st-readout", words(chart.more_words || chart.note_words)),
      );
      return block;
    }

    var frame = el("div", "st-chart-body");
    var plot = el("div", "st-plot-box");
    plot.setAttribute("tabindex", "0");
    plot.setAttribute("role", "img");
    plot.setAttribute("aria-label", words(chart.title));
    plot.style.touchAction = "pan-y pinch-zoom";
    var box = chartSvg();
    var cursor = null;
    var current = null;
    redraw = function () {
      while (box.firstChild) box.removeChild(box.firstChild);
      DRAW_CHART[m.kind](box, m, state.isolated);
      cursor = cursorOf(m, current);
      box.appendChild(cursor);
      var buttons = titleRow.querySelectorAll("button.st-key");
      for (var j = 0; j < buttons.length; j++)
        buttons[j].setAttribute(
          "aria-pressed",
          String(state.isolated) === buttons[j].getAttribute("data-series")
            ? "true"
            : "false",
        );
    };
    redraw();
    plot.appendChild(box);
    var marker = chart.marker && chart.marker.w ? [chart.marker] : [];
    (chart.marks || [])
      .filter(function (mk) {
        return mk && (mk.kind === "vline" || mk.kind === "point") && mk.w;
      })
      .concat(marker)
      .forEach(function (mk) {
        var lab = el("span", "st-mark-w", mk.w);
        lab.style.left = (mk.at / CMAX) * 100 + "%";
        if (mk.at > CMAX * 0.6) lab.className += " st-mark-w-left";
        plot.appendChild(lab);
      });
    var yTicks = ticksOf(chart.y, "y");
    var xTicks = ticksOf(chart.x, "x");
    // CARD-RENDER #2: every chart names its measures, as the map does: the
    // y axis's served name up its left edge, the x axis's under its ticks.
    var yName = chart.y && chart.y.label;
    // MCP-FINAL round 6: "Growth of 1" was both the chart's title and its y
    // axis's name; a name the title already says is not drawn twice.
    var sameAsTitle =
      yName &&
      chart.title &&
      String(yName).trim().toLowerCase() ===
        String(chart.title).trim().toLowerCase();
    if (yName && !sameAsTitle) {
      var yn = el("span", "st-axis-name st-axis-name-y", words(yName));
      yn.title = yName;
      frame.appendChild(yn);
    }
    frame.appendChild(yTicks);
    frame.appendChild(plot);
    block.appendChild(frame);
    block.appendChild(xTicks);
    if (m.kind === "heat") ringOverlay(plot, chart);
    var hk = m.kind === "heat" ? heatKey(chart, m, copy) : null;
    if (hk) block.appendChild(hk);
    else if (chart.x && chart.x.label) {
      var xr = el("div", "st-axis-row");
      var xn = el("span", "st-axis-name st-axis-name-x", words(chart.x.label));
      xn.title = chart.x.label;
      xr.appendChild(xn);
      block.appendChild(xr);
    }
    requestAnimationFrame(function () {
      // CARD-360: a legend that crowds the title ("Grow… — Momentum
      // rotation v4 — Momentum rotati…" at 360 px) takes its own line
      // under it; the plot gives up that line inside the key's fixed slot.
      // Laid out before the ticks, which fit the plot's final size.
      if (
        keys &&
        (name.scrollWidth > name.clientWidth + 1 ||
          keys.scrollWidth > keys.clientWidth + 1)
      )
        titleRow.setAttribute("data-wrap", "1");
      fitTicks(block, yTicks, xTicks);
    });

    var readout = el("div", "st-readout");
    readout.setAttribute("aria-live", "polite");
    var coarse =
      window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    var hint = words(coarse ? copy.hint_touch : copy.hint_pointer);
    // MCP-CARDS (RESEARCH.md §2.4): on the face the readout rests on the last
    // point, which teaches the crosshair; no instruction line is printed.
    var rest = o.face && countOf(m) ? readWords(m, countOf(m) - 1) : null;
    var idle = o.face ? rest || "" : hint;
    var readText = el("span", "st-readout-w", o.face ? "" : idle);
    var faceShow = function (i) {
      var at = faceRead(m, i);
      readText.textContent = "";
      if (at.bold) readText.appendChild(el("span", "st-read-v", at.bold));
      if (at.rest)
        readText.appendChild(
          el("span", "st-read-l", (at.bold ? SEP : "") + at.rest),
        );
    };
    if (o.face) faceShow(countOf(m) ? countOf(m) - 1 : null);
    readout.appendChild(readText);
    if (o.face) titleRow.appendChild(readout);
    else block.appendChild(readout);
    // D-354 (2): the served key sentence repeats a drawn key line, so it is
    // drawn only where no key line is, as its own line that wraps.
    // MCP-CARDS (D-420): the face says its provenance once, in its caption;
    // the chart's own note is the full study's (Expand).
    if (!o.face && !hk && chart.note_words)
      block.appendChild(el("p", "st-chart-note", words(chart.note_words)));

    // A selected cell or bar (13 §4.1): its link in the readout's fixed
    // right-hand slot, to that setting or bar in Keel.
    var links = data.links || {};
    var v = o.v || (chart.title ? encodeURIComponent(chart.title) : null);
    var selLabel = m.kind === "heat" ? copy.open_setting : copy.open_bar;
    var selSlot = el("span", "st-readout-link");
    readout.appendChild(selSlot);
    var selected = null;
    function select(i) {
      selected = i;
      selSlot.textContent = "";
      var token = i == null ? null : selOf(m, i);
      block.setAttribute("data-sel", token || "");
      state.sel = token;
      if (token && links.select && v && selLabel)
        selSlot.appendChild(
          linkButton(
            selLabel,
            fill(links.select, { v: v, sel: token }),
            "st-link st-link-small",
          ),
        );
    }
    function show(i) {
      current = i;
      if (cursor && cursor.parentNode) cursor.parentNode.removeChild(cursor);
      cursor = cursorOf(m, i);
      box.appendChild(cursor);
      if (o.face)
        faceShow(i == null ? (countOf(m) ? countOf(m) - 1 : null) : i);
      else readText.textContent = i == null ? idle : readWords(m, i) || idle;
      block.setAttribute("data-at", i == null ? "" : String(i));
    }
    function fromEvent(ev) {
      var r = plot.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      return indexAt(
        m,
        Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
        Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)),
      );
    }
    if (countOf(m)) {
      plot.addEventListener("pointermove", function (ev) {
        show(fromEvent(ev));
      });
      plot.addEventListener("pointerdown", function (ev) {
        var at = fromEvent(ev);
        show(at);
        if (m.kind === "heat" || m.kind === "bars")
          select(at === selected ? null : at);
      });
      plot.addEventListener("pointerleave", function (ev) {
        if (ev.pointerType !== "touch") show(null);
      });
      plot.addEventListener("keydown", function (ev) {
        var n = countOf(m);
        var step = m.kind === "heat" ? m.c.cols : 0;
        var i = current == null ? -1 : current;
        var next;
        if (ev.key === "ArrowRight") next = Math.min(n - 1, i + 1);
        else if (ev.key === "ArrowLeft") next = Math.max(0, i - 1);
        else if (ev.key === "ArrowUp" && step)
          next = Math.min(n - 1, Math.max(0, i) + step);
        else if (ev.key === "ArrowDown" && step)
          next = Math.max(0, Math.max(0, i) - step);
        else if (ev.key === "Home") next = 0;
        else if (ev.key === "End") next = n - 1;
        else if (ev.key === "Escape") {
          next = null;
          select(null); // the selected cell's link goes with it
        } else if (ev.key === "Enter" && current != null) {
          // Enter selects the cell or bar under the readout and focuses its link.
          if (m.kind === "heat" || m.kind === "bars") {
            select(current);
            var a = selSlot.querySelector("a");
            if (a) a.focus();
          }
          ev.preventDefault();
          return;
        } else return;
        ev.preventDefault();
        show(next);
      });
    }
    if (m.kind === "heat")
      requestAnimationFrame(function () {
        cellValues(plot, m);
      });
    return block;
  }

  // ── The pieces every layout shares ─────────────────────────────────────

  /** The served card data, or {} when absent or of a major this card does
   *  not know (13 §4.4: then the summary layout from `card_summary` alone). */
  function cardDataOf(env) {
    var d = env.card_data;
    if (!d || typeof d !== "object") return {};
    var schema = String(d.schema || "");
    var major = schema.split("/").pop().split(".")[0];
    return major === CARD_DATA_MAJOR ? d : {};
  }

  /**
   * The header (CARD-RENDER #3, Screener's calm head): line 1 the title (cut
   * at a word end, never mid-word), ONE version pill and the kind chip;
   * line 2, muted, the state words and the maker's served words, then the
   * note chips ("Linked", "v4 Draft not run"), which never drop at 360 px.
   * `stateWords` overrides the served state words (null: none).
   */
  function head(env, summary, data, stateWords) {
    var study = env.study || {};
    H.header({
      name: study.title || summary.title || null,
      version: summary.version != null ? summary.version : study.version,
      kind: summary.kind_label || study.kind_label || null,
    });
    // D-352 (4): no "Keel" wordmark glued before the title.
    var brand = document.querySelector("#card-head .brand");
    if (brand) brand.hidden = true;
    var headEl = document.getElementById("card-head");
    if (!headEl) return;
    var old = headEl.querySelector(".st-sub");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var sub = el("div", "st-sub");
    (data.chips || []).slice(0, MAX_NOTES).forEach(function (note) {
      if (typeof note !== "string" || !note) return;
      var n = el("span", "chip muted note st-note-chip", note);
      n.title = note;
      sub.appendChild(n);
    });
    var said = [
      stateWords === undefined
        ? summary.state_words || study.state_words
        : stateWords,
      summary.maker_words,
    ].filter(function (w) {
      return typeof w === "string" && w;
    });
    if (said.length) {
      var line = el("span", "st-sub-w", said.join(SEP));
      line.title = said.join(SEP);
      sub.appendChild(line);
    }
    headEl.appendChild(sub);
    var nm = headEl.querySelector(".head-name");
    if (nm) fitWords(nm);
  }

  /**
   * Cut a one-line text at the end of a word so it fits its box, with an
   * ellipsis (CARD-RENDER #3: "MA Cross…en Cross) Strategy" cut mid-word).
   * The whole text stays in `title`. Re-run on the next frame (layout).
   */
  function fitWords(node) {
    var full = node.getAttribute("data-full");
    if (full === null) {
      full = node.textContent;
      node.setAttribute("data-full", full);
      node.title = full;
    }
    var fit = function () {
      node.textContent = full;
      if (!node.clientWidth || node.scrollWidth <= node.clientWidth + 1) return;
      var parts = full.split(" ");
      for (var n = parts.length - 1; n > 0; n--) {
        node.textContent =
          parts
            .slice(0, n)
            .join(" ")
            .replace(/[,·:;(]+$/, "") + "…";
        if (node.scrollWidth <= node.clientWidth + 1) return;
      }
    };
    fit();
    requestAnimationFrame(fit);
  }

  /**
   * Cut a one-line " · " list at a segment end so it fits its box, with
   * " …" (CARD-360: a tile's sample read "353 days · 353 effective day…"
   * at 360 and 760 px). A text whose first segment alone does not fit is
   * left whole for the CSS ellipsis. The tile's title says it whole.
   */
  function fitSegments(node) {
    var full = node.textContent;
    var parts = full.split(SEP);
    if (parts.length < 2) return;
    var fit = function () {
      node.textContent = full;
      if (!node.clientWidth || node.scrollWidth <= node.clientWidth + 1) return;
      for (var n = parts.length - 1; n > 0; n--) {
        node.textContent = parts.slice(0, n).join(SEP) + " …";
        if (node.scrollWidth <= node.clientWidth + 1) return;
      }
      node.textContent = full;
    };
    requestAnimationFrame(fit);
  }

  /** The numbers: only what is served (D-332 (1)), up to four. */
  function tiles(summary) {
    var rows = (summary.numbers || []).slice(0, MAX_NUMBERS);
    var wrap = el("div", "st-numbers");
    wrap.setAttribute("data-n", String(rows.length));
    rows.forEach(function (n) {
      var cell = el("div", "st-number");
      // A served number with no words (a v1 server) → the em dash from the
      // CSS, never the raw value.
      // D-380: a probability is served as a sentence ("0.45 chance the edge
      // is real with one version known"); in the one-line number style it
      // read "0.45 chance the ed…" (MCP-FINAL, round 6). Its leading number
      // keeps the big style and the served words follow it on their own
      // wrapping line, the served string unchanged.
      var said = words(n.value_words);
      var split = /^(\S*\d\S*)\s+(.*[A-Za-z].*)$/.exec(said);
      if (split) {
        cell.className = "st-number st-number-words";
        cell.appendChild(el("span", "st-value", split[1]));
        cell.appendChild(el("span", "st-value-words", split[2]));
      } else {
        cell.appendChild(el("span", "st-value", said));
      }
      cell.appendChild(el("span", "st-label", words(n.label)));
      var sample = el("span", "st-sample", words(n.sample_words));
      cell.appendChild(sample);
      fitSegments(sample);
      // The tile's lines ellipsise in its fixed 74 px (a phone-width sample
      // "Mar 23, 2025 to Oct 29, 2…"); its title says every one whole.
      var whole = [n.label, split ? said : "", n.sample_words]
        .filter(function (t) {
          return typeof t === "string" && t;
        })
        .join(" · ");
      if (whole) cell.title = whole;
      wrap.appendChild(cell);
    });
    return wrap;
  }

  /**
   * The lead (13 §2.2 slot 2): the served line, never one the header already
   * says (CARD-RENDER #6: "Running · 6 of 48 settings" twice) and never the
   * kind's name; then the headline. An empty line keeps its slot.
   */
  function leadOf(summary) {
    var kind = (summary.kind_label || {}).name;
    var said = function (t) {
      return (
        typeof t === "string" &&
        t &&
        t !== summary.state_words &&
        t !== kind &&
        t
      );
    };
    var lead =
      said(summary.lead) ||
      (summary.headline &&
        (said(summary.headline.text) || said(summary.headline.sentence))) ||
      "";
    var p = el("p", "st-lead", lead);
    if (lead) p.title = lead;
    return p;
  }

  function footingOf(summary) {
    var p = el("p", "st-footing", words(summary.footing_line));
    if (summary.footing_line) p.title = summary.footing_line;
    return p;
  }

  /**
   * The key chart slot: the served CardChart (title, axes, readout), else
   * the thumb filling the whole slot (no empty title or axis band, D-332
   * (3)), else the top rows.
   */
  function keySlot(summary, data, state) {
    var slot = el("div", "st-key-slot");
    if (data.key) {
      try {
        slot.appendChild(
          chartBlock(data.key, data, { state: state, onState: saveState }),
        );
        slot.setAttribute("data-key", "chart");
        return slot;
      } catch (e) {
        if (!e.thumb) throw e;
        slot.setAttribute("data-chart-error", e.message);
        if (window.console && console.error)
          console.error("keel study card: undecodable chart", data.key);
      }
    }
    var top = summary.top5 && summary.top5.rows;
    if (drawThumbInto(slot, summary.thumb, top, slot))
      slot.setAttribute("data-key", summary.thumb ? "thumb" : "rows");
    return slot;
  }

  function setFrame(layout, extra) {
    var b = document.body;
    if (!b) return;
    b.setAttribute("data-tpl", TPL);
    if (layout) b.setAttribute("data-layout", layout);
    else b.removeAttribute("data-layout");
    b.removeAttribute("data-strip");
    b.removeAttribute("data-size");
    b.removeAttribute("data-no-hero");
    b.removeAttribute("data-no-expand");
    b.removeAttribute("data-terminal");
    b.removeAttribute("data-face");
    b.setAttribute("data-owns-link", "0");
    Object.keys(extra || {}).forEach(function (k) {
      b.setAttribute(k, extra[k]);
    });
    var headEl = document.getElementById("card-head");
    if (headEl) headEl.hidden = false;
  }

  function savedState() {
    var s = typeof H.widgetState === "function" ? H.widgetState() : {};
    return s && typeof s === "object" ? s : {};
  }

  function saveState(s) {
    if (typeof H.setWidgetState === "function")
      H.setWidgetState({ tab: s.tab, sel: s.sel, isolated: s.isolated });
  }

  // ── Layouts ────────────────────────────────────────────────────────────

  /** A strip tap: fullscreen on that tab where offered, else the chart in Keel. */
  function openChart(env, data, entry) {
    var modes = H.availableDisplayModes || [];
    if (modes.indexOf("fullscreen") >= 0) {
      var s = savedState();
      s.tab = "v" + entry.index;
      saveState(s);
      H.requestDisplayMode("fullscreen");
      return;
    }
    var links = data.links || {};
    var url = links.chart ? fill(links.chart, { v: entry.v }) : env.hero_url;
    if (url) H.openLink(url);
  }

  /** A table's strip thumbnail: its first three rows, first two cells. */
  function miniTable(chart) {
    var mini = el("span", "st-mini-checks st-mini-table");
    (chart.rows || []).slice(0, 3).forEach(function (row) {
      var line = el("span", "st-mini-row");
      line.appendChild(el("span", "st-mini-cell", words(row[0])));
      if (row.length > 1)
        line.appendChild(el("span", "st-mini-cell st-mini-val", words(row[1])));
      mini.appendChild(line);
    });
    return mini;
  }

  /** The strip of more charts (13 §2.2 slot 6): up to 4 (3 narrow). */
  function stripOf(env, data) {
    var entries = (data.charts || []).filter(function (e) {
      return e && e.title;
    });
    var shown = entries.slice(0, H.isNarrow() ? 3 : 4);
    var strip = el("div", "st-strip");
    shown.forEach(function (entry) {
      var b = el("button", "st-strip-item");
      b.type = "button";
      var pic = el("span", "st-strip-pic");
      try {
        if (
          entry.form === "chart" &&
          entry.chart &&
          DRAW_CHART[entry.chart.kind]
        ) {
          var mm = decodeChart(entry.chart);
          var mini = chartSvg();
          DRAW_CHART[mm.kind](mini, mm, null);
          pic.appendChild(mini);
        } else if (
          entry.form === "chart" &&
          entry.chart &&
          entry.chart.kind === "table" &&
          (entry.chart.rows || []).length
        ) {
          // CARD-FINAL B2: a table has no picture to draw small, so its
          // thumbnail is its first rows as served words (it was an empty box).
          pic.appendChild(miniTable(entry.chart));
        } else if (entry.thumb) {
          drawThumbInto(pic, entry.thumb, null, b);
        } else if (
          entry.family === "Checks" &&
          data.checks &&
          (data.checks.rows || []).length
        ) {
          // "Your checks" as short labelled rows (D-337 (6)): the misses
          // first, each its glyph and its served label.
          var mini = el("span", "st-mini-checks");
          var plainMini = noBars(data);
          var rowsSorted = (data.checks.rows || [])
            .slice()
            .sort(function (a, b) {
              return (a.met === false ? 0 : 1) - (b.met === false ? 0 : 1);
            });
          rowsSorted.slice(0, 3).forEach(function (r) {
            var g = el("span", "st-mini-check", words(r.label));
            // D-407 (2): a Check with no bars set draws no met glyph
            g.setAttribute("data-state", checkState(r, plainMini));
            mini.appendChild(g);
          });
          pic.appendChild(mini);
        } else if (entry.state_words && entry.family !== "Checks") {
          // A Checks entry never says "Not run yet": with no rows served it
          // draws its title only (D-352 (1)).
          pic.appendChild(el("span", "st-strip-w", entry.state_words));
        } else if (
          entry.chart &&
          entry.chart.kind === "table" &&
          (entry.chart.note_words || entry.chart.more_words)
        ) {
          // A table with no rows to show says its served sentence
          // ("Same footing."), the words its tab draws.
          pic.appendChild(
            el(
              "span",
              "st-strip-w",
              entry.chart.note_words || entry.chart.more_words,
            ),
          );
        } else if (
          entry.family !== "Checks" &&
          (data.copy || {}).full_chart_in_keel
        ) {
          // CARD-FINAL B2: a chart served by title only says where it is
          // drawn, never an empty box.
          pic.appendChild(
            el("span", "st-strip-w", data.copy.full_chart_in_keel),
          );
        }
      } catch (e) {
        if (!e.thumb) throw e;
        b.setAttribute("data-thumb-error", e.message);
      }
      b.appendChild(pic);
      b.appendChild(el("span", "st-strip-title", entry.title));
      b.title = entry.title;
      b.setAttribute("aria-label", entry.title);
      b.addEventListener("click", function () {
        openChart(env, data, entry);
      });
      strip.appendChild(b);
    });
    // The served "+N more charts" names what the strip leaves out.
    if (data.copy && data.copy.more_charts && data.more_charts_total > 0)
      strip.appendChild(el("span", "st-strip-more", data.copy.more_charts));
    return strip;
  }

  function hasStrip(data) {
    return !!((data.charts && data.charts.length) || data.more_charts_total);
  }

  // ── The face (MCP-CARDS, D-420; build/lane-scratch/MCP-CARDS/DESIGN.md) ──
  // The inline card at its content height (RESEARCH.md §1 R3: both hosts ask
  // for it; supersedes D-332 / D-381's reserved frames inline). One idea in
  // every state: one dot is one unit of evidence, a run or a bar you set.
  // Every word is served: the title, the meta words, card_data.hero and
  // card_data.tiles (research.card_summary.card_face), the steps, the copy.

  var MAX_DOTS = 96;

  function faceFrame(layout, extra) {
    setFrame(layout, extra);
    if (document.body) document.body.setAttribute("data-face", "1");
    var headEl = document.getElementById("card-head");
    if (headEl) headEl.hidden = true;
  }

  /** Title, then one muted line: kind, version, notes, state, maker. */
  function faceHead(env, summary, data, stateWords) {
    var study = env.study || {};
    var head = el("div", "sf-head");
    var name = study.title || summary.title || "";
    var t = el("h2", "sf-title", words(name));
    if (name) t.title = name;
    head.appendChild(t);
    var kind = (summary.kind_label || study.kind_label || {}).short;
    var version = summary.version != null ? summary.version : study.version;
    var vWord = version != null ? "v" + version : null;
    var chips = data.chips || [];
    // a note chip that names the version ("v1 Draft not run") says it once
    if (
      vWord &&
      chips.some(function (c) {
        return String(c).indexOf(vWord + " ") === 0;
      })
    )
      vWord = null;
    var said = [kind, vWord]
      .concat(chips)
      .concat([
        stateWords === undefined
          ? summary.state_words || study.state_words
          : stateWords,
        summary.maker_words,
      ])
      .filter(function (w) {
        return typeof w === "string" && w;
      });
    var meta = el("p", "sf-meta", said.join(SEP));
    if (said.length) meta.title = said.join(SEP);
    head.appendChild(meta);
    return head;
  }

  /** The hero: its value big, its label under it; `aside` to its right. */
  function faceHero(hero, aside) {
    var row = el("div", "sf-hero");
    var n = el("div", "sf-hero-n");
    n.appendChild(el("span", "sf-hero-v", words(hero.value)));
    n.appendChild(el("span", "sf-hero-l", words(hero.label)));
    if (hero.title) n.title = hero.title;
    row.appendChild(n);
    if (aside) row.appendChild(aside);
    return row;
  }

  /** One pip per bar you set: met filled, not met hollow, not measured dashed. */
  function facePips(data) {
    var rows = ((data.checks && data.checks.rows) || []).filter(function (r) {
      return r && !r.locked;
    });
    if (!rows.length || noBars(data)) return null;
    // met first, then not met, then not measured (integrator polish 5): the
    // count reads at a glance; each pip's title names its bar
    var rank = function (r) {
      return r.met === true ? 0 : r.met === false ? 1 : 2;
    };
    rows = rows
      .map(function (r, k) {
        return { r: r, k: k };
      })
      .sort(function (a, b) {
        return rank(a.r) - rank(b.r) || a.k - b.k;
      })
      .map(function (x) {
        return x.r;
      });
    var wrap = el("div", "sf-pips");
    wrap.setAttribute("role", "list");
    rows.forEach(function (r) {
      var pip = el("span", "sf-pip");
      pip.setAttribute("role", "listitem");
      pip.setAttribute(
        "data-state",
        r.met === true ? "met" : r.met === false ? "not_met" : "unmeasured",
      );
      var said = [r.label, r.value_words, r.bar_words, checkWord(r, data)]
        .filter(function (w) {
          return typeof w === "string" && w;
        })
        .join(SEP);
      pip.title = said;
      pip.setAttribute("aria-label", said);
      wrap.appendChild(pip);
    });
    return wrap;
  }

  /** A check row's met words, from the served copy. */
  function checkWord(r, data) {
    var copy = data.copy || {};
    if (r.met === true) return copy.met;
    if (r.met === false) return copy.not_met;
    return copy.not_measured;
  }

  /** Dots for runs: done filled, running ringed, failed crossed, the rest hollow. */
  function faceDots(total, done, running, failed, hollow) {
    var n = Math.max(0, total | 0);
    if (!n) return null;
    var per = Math.ceil(n / MAX_DOTS);
    var count = Math.ceil(n / per);
    var wrap = el("div", "sf-dots");
    wrap.setAttribute("aria-hidden", "true");
    for (var i = 0; i < count; i++) {
      var at = i * per;
      var d = el("span", "sf-dot");
      var state = hollow
        ? "waiting"
        : at < done
          ? "done"
          : at < done + failed
            ? "failed"
            : at < done + failed + running
              ? "running"
              : "waiting";
      d.setAttribute("data-state", state);
      wrap.appendChild(d);
    }
    return wrap;
  }

  /** Up to four tiles: the value, its short label, the bar it is held to. */
  function faceTiles(tiles) {
    var shown = (tiles || []).slice(0, MAX_NUMBERS);
    if (!shown.length) return null;
    var wrap = el("div", "sf-tiles");
    wrap.setAttribute("data-n", String(shown.length));
    shown.forEach(function (t) {
      var cell = el("div", "sf-tile");
      if (t.state) cell.setAttribute("data-state", t.state);
      var val = el("span", "sf-tile-v");
      // a bar you set: the tile wears its pip (met filled, not met hollow),
      // the same mark as the hero's, never a verdict word (D-407: only when set)
      if (t.state) {
        var mark = el("span", "sf-pip sf-tile-pip");
        mark.setAttribute("data-state", t.state);
        mark.setAttribute("aria-hidden", "true");
        val.appendChild(mark);
      }
      val.appendChild(document.createTextNode(words(t.value)));
      cell.appendChild(val);
      cell.appendChild(el("span", "sf-tile-l", words(t.label)));
      if (t.sub) cell.appendChild(el("span", "sf-tile-s", words(t.sub)));
      if (t.title) cell.title = t.title;
      wrap.appendChild(cell);
    });
    return wrap;
  }

  /** The key chart at face size, its readout resting on the last point. */
  function faceChart(summary, data) {
    // the key slot's class too, so every key-chart reader (hover, ticks) reads it
    var slot = el("div", "sf-chart st-key-slot");
    if (data.key) {
      try {
        slot.appendChild(
          chartBlock(data.key, data, {
            state: savedState(),
            onState: saveState,
            face: true,
          }),
        );
        slot.setAttribute("data-key", "chart");
        return slot;
      } catch (e) {
        if (!e.thumb) throw e;
        slot.setAttribute("data-chart-error", e.message);
      }
    }
    if (drawThumbInto(slot, summary.thumb, null, slot)) {
      slot.setAttribute("data-key", "thumb");
      return slot;
    }
    return null;
  }

  /** A server older than the face (no card_data.hero): its first served number
   *  is the hero and the next three the tiles, their labels whole. */
  function servedFace(summary, data) {
    if (data.hero) return { hero: data.hero, tiles: data.tiles || [] };
    var nums = (summary.numbers || []).filter(function (n) {
      return n && (n.value_short || n.value_words);
    });
    var as = function (n) {
      return {
        value: n.value_short || n.value_words,
        label: n.label,
        title: [n.label, n.value_words, n.sample_words]
          .filter(function (w) {
            return typeof w === "string" && w;
          })
          .join(SEP),
      };
    };
    return nums.length
      ? { hero: as(nums[0]), tiles: nums.slice(1, 4).map(as) }
      : { hero: null, tiles: [] };
  }

  /** A served chart drawn small, marks only (a thumbnail, a dashboard tile, a
   *  check row's chart); null when it draws no mark (never an empty box). */
  function miniChart(chart, cls) {
    if (!chart || !DRAW_CHART[chart.kind]) return null;
    var m;
    try {
      m = decodeChart(chart);
    } catch (e) {
      if (!e.thumb) throw e;
      return null;
    }
    var box = chartSvg();
    box.setAttribute("class", "st-plot " + (cls || "sf-mini"));
    DRAW_CHART[m.kind](box, m, null);
    var marks = box.querySelectorAll(
      ".st-line, .st-bar, .st-bin, .st-dot, .st-cell",
    ).length;
    return marks ? box : null;
  }

  /** The gallery entries by view index. */
  function entriesByIndex(data) {
    var by = {};
    (data.charts || []).forEach(function (e) {
      if (e && typeof e.index === "number") by[e.index] = e;
    });
    return by;
  }

  /** Expand on the dashboard where the host offers fullscreen, else Keel. */
  function openAll(env) {
    var modes = H.availableDisplayModes || [];
    if (modes.indexOf("fullscreen") >= 0) {
      var s = savedState();
      s.tab = null;
      saveState(s);
      H.requestDisplayMode("fullscreen");
      return;
    }
    var url = (env.links && env.links.app) || env.hero_url;
    if (url) H.openLink(url);
  }

  /**
   * MCP-CARDS (D-420d): up to 3 small charts of the next views under the key
   * chart (card_data.thumbs: views the server judged to draw a picture, in the
   * study's order) and the served "+N more charts". A tap opens Expand on that
   * chart (requestDisplayMode inside the click), or the chart in Keel where
   * the host lists no fullscreen.
   */
  function faceThumbs(env, data) {
    var by = entriesByIndex(data);
    var row = el("div", "sf-thumb-row");
    (data.thumbs || []).forEach(function (i) {
      var e = by[i];
      var pic = e && e.form === "chart" ? miniChart(e.chart, "sf-mini") : null;
      if (!pic) return;
      var b = el("button", "sf-thumb");
      b.type = "button";
      b.setAttribute("data-index", String(e.index));
      if (e.v) b.setAttribute("data-v", e.v);
      b.title = e.title;
      var frame = el("span", "sf-thumb-pic");
      frame.appendChild(pic);
      b.appendChild(frame);
      b.appendChild(el("span", "sf-thumb-t", words(e.title)));
      b.addEventListener("click", function () {
        openChart(env, data, e);
      });
      row.appendChild(b);
    });
    if (!row.children.length) return null;
    var wrap = el("div", "sf-thumbs");
    wrap.appendChild(row);
    var more = (data.copy || {}).more_views;
    if (more) {
      var m = el("button", "button-link button-quiet sf-more", more);
      m.type = "button";
      m.addEventListener("click", function () {
        openAll(env);
      });
      wrap.appendChild(m);
    }
    return wrap;
  }

  /** A done study (and a run done inside its wait): hero, key chart, tiles, provenance. */
  function faceStudy(env, body, layout) {
    var summary = env.card_summary || {};
    var data = cardDataOf(env);
    var face = servedFace(summary, data);
    faceFrame(layout || "face-study", {
      "data-key": data.key ? "chart" : "thumb",
    });
    body.appendChild(faceHead(env, summary, data));
    if (face.hero) body.appendChild(faceHero(face.hero, facePips(data)));
    var chart = faceChart(summary, data);
    if (chart) body.appendChild(chart);
    var thumbs = faceThumbs(env, data);
    if (thumbs) body.appendChild(thumbs);
    var tiles = faceTiles(face.tiles);
    if (tiles) body.appendChild(tiles);
    if (summary.footing_line)
      body.appendChild(el("p", "sf-caption", words(summary.footing_line)));
  }

  /** In flight, queued or stopped: the count, the run dots, the step going and the next. */
  function faceRun(env, body, opts) {
    var o = opts || {};
    var summary = env.card_summary || {};
    var data = cardDataOf(env);
    var p = env.progress || {};
    faceFrame("face-run", o.stopped ? { "data-terminal": "1" } : null);
    if (document.body) document.body.setAttribute("data-no-expand", "1");
    body.setAttribute(
      "data-run",
      o.failed ? "failed" : o.stopped ? "stopped" : "running",
    );
    // the meta line says the state without the count the hero says (polish 1)
    var hs = data.hero && data.hero.state;
    body.appendChild(faceHead(env, summary, data, hs || undefined));
    var by = p.by_status || {};
    var aside = o.failed
      ? p.stopped || summary.state_words || p.words
      : o.stopped
        ? p.receipt || p.stopped || p.words
        : p.queue || p.time_words || null;
    var dots = faceDots(
      p.children,
      p.done || 0,
      by.running || 0,
      (by.failed || 0) + (by.cancelled || 0),
      false,
    );
    var bar = null;
    if (dots) {
      // the dots sit beside the count, as a Check's pips sit beside its bars
      bar = el("div", "sf-progress");
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", String(p.children || 0));
      bar.setAttribute("aria-valuenow", String(p.done || 0));
      if (p.words) bar.setAttribute("aria-valuetext", p.words);
      bar.appendChild(dots);
    }
    // The count (or the dots alone) and the served words under it, in one
    // column with its own gap: each block keeps its own line at every width
    // (the words once rose 6 px into a hero-less failed card's dots).
    var lead = el("div", "sf-run-lead");
    // A run's own hero (its count, served with its state words) carries the
    // dots beside it. A stopped study's result hero ("1 of 9 bars met") is
    // not about runs: it keeps its pips, and the run dots take their own row.
    if (data.hero && data.hero.state)
      lead.appendChild(faceHero(data.hero, bar));
    else {
      if (data.hero) lead.appendChild(faceHero(data.hero, facePips(data)));
      if (bar) lead.appendChild(bar);
    }
    if (aside) {
      // a stop's served sentences each take a line (one run-on block read as a wall)
      var said = el("div", "sf-hero-aside");
      sentencesOf(words(aside)).forEach(function (line) {
        said.appendChild(el("p", "sf-aside-line", line));
      });
      lead.appendChild(said);
    }
    if (lead.children.length) body.appendChild(lead);
    var steps = (data.steps || []).slice(0, STEPS_FACE);
    if (steps.length) {
      var list = el("ul", "sf-steps");
      steps.forEach(function (st) {
        var li = el("li", "sf-step");
        if (st.state) li.setAttribute("data-state", st.state);
        li.appendChild(el("span", "sf-step-l", words(st.label)));
        li.appendChild(el("span", "sf-step-w", words(st.words)));
        list.appendChild(li);
      });
      body.appendChild(list);
      var more = (data.copy || {}).more_steps;
      if (more) body.appendChild(el("p", "sf-caption", more));
    }
  }

  /** Served words, one sentence a line: split after a sentence's full stop
   *  (layout only; every word stays the server's). */
  function sentencesOf(text) {
    return String(text || "")
      .replace(/([.!?])\s+(?=\S)/g, "$1\n")
      .split("\n")
      .filter(function (line) {
        return line;
      });
  }

  /** A saved Draft that never ran: what the plan will do, before anyone spends it. */
  function facePlan(env, body) {
    var summary = env.card_summary || {};
    var study = env.study || {};
    var data = cardDataOf(env);
    var draft = study.draft || {};
    faceFrame("face-plan", { "data-no-expand": "1" });
    body.appendChild(faceHead(env, summary, data, null));
    var row = el("div", "sf-plan");
    var work = draft.words || (data.notes || {}).draft;
    if (work) row.appendChild(el("span", "sf-plan-w", words(work)));
    var dots = faceDots(data.runs || 0, 0, 0, 0, true);
    if (dots) row.appendChild(dots);
    body.appendChild(row);
    var steps = data.steps || [];
    if (steps.length) {
      var list = el("ul", "sf-plan-steps");
      steps.slice(0, STEPS_FACE_PLAN).forEach(function (st) {
        var li = el("li", "sf-plan-step");
        li.appendChild(el("span", "sf-step-l", words(st.label)));
        if (st.words) li.appendChild(el("span", "sf-step-w", words(st.words)));
        list.appendChild(li);
      });
      body.appendChild(list);
      var more = (data.copy || {}).more_steps;
      if (more) body.appendChild(el("p", "sf-caption", more));
    }
    // what running it uses of the account's allowance (served, names no plan)
    if (study.counter)
      body.appendChild(el("p", "sf-caption", words(study.counter)));
  }

  /** The list: up to five studies, each a picture, its name, kind and state, its lead number. */
  function faceList(env, body) {
    var data = cardDataOf(env);
    faceFrame("face-list", { "data-no-expand": "1" });
    var head = el("div", "sf-head");
    head.appendChild(el("h2", "sf-title", words(data.head_words)));
    body.appendChild(head);
    var rows = env.studies || [];
    var extra = data.rows || [];
    var n = Math.min(rows.length, extra.length || rows.length, MAX_LIST);
    var list = el("ul", "sf-list");
    for (var i = 0; i < n; i++) {
      var r = rows[i];
      var x = extra[i] || {};
      var li = el("li", "sf-row");
      var url = x.link || null;
      var a = el(url ? "a" : "div", "sf-row-link");
      if (url) {
        a.href = url;
        a.addEventListener(
          "click",
          (function (u) {
            return function (ev) {
              ev.preventDefault();
              H.openLink(u);
            };
          })(url),
        );
      }
      var pic = el("span", "sf-row-pic");
      if (x.thumb) {
        try {
          drawThumbInto(pic, x.thumb, null, pic);
        } catch (e) {
          if (!e.thumb) throw e;
        }
      }
      a.appendChild(pic);
      var text = el("span", "sf-row-text");
      text.appendChild(el("span", "sf-row-title", words(r.title)));
      var meta = [r.kind_label && r.kind_label.short, r.state_words].filter(
        function (w) {
          return typeof w === "string" && w;
        },
      );
      text.appendChild(el("span", "sf-row-meta", meta.join(SEP)));
      a.appendChild(text);
      if (x.lead_number_value) {
        var num = el("span", "sf-row-num");
        num.appendChild(el("span", "sf-row-v", x.lead_number_value));
        num.appendChild(el("span", "sf-row-l", words(x.lead_number_label)));
        num.title = x.lead_number_words || "";
        a.appendChild(num);
      }
      if (r.title) a.title = r.title;
      li.appendChild(a);
      list.appendChild(li);
    }
    var empty = (data.copy || {}).no_studies;
    if (!rows.length && empty) list.appendChild(el("li", "sf-empty", empty));
    body.appendChild(list);
    if (data.more_words)
      body.appendChild(el("p", "sf-caption", words(data.more_words)));
  }

  var STEPS_FACE = 2;
  var STEPS_FACE_PLAN = 5;

  /** The study state a card reads (the read's own, else its card's). */
  function stateOf(env) {
    var summary = env.card_summary || {};
    return String((env.study || {}).state || summary.state || "");
  }

  /** A run frame's state: in flight (updates in place) or stopped short. */
  var IN_FLIGHT = { running: 1, cancel_requested: 1 };

  function renderStudy(env, body) {
    var summary = env.card_summary || {};
    var data = cardDataOf(env);
    var state = stateOf(env);
    // D-381 (2), 13 §4.3: a get of a study still running (or stopping) draws
    // the run card's progress inside the study's reserved frame; the body
    // box takes the extra height, so the frame is filled, never blank.
    // MCP-CARDS (D-420): a study in flight is its run face; a stopped one with
    // no key chart its run face, frozen; anything with a result its face.
    if (IN_FLIGHT[state]) {
      faceRun(env, body);
      return;
    }
    if (state === "cancelled" && !data.key) {
      faceRun(env, body, { stopped: true });
      return;
    }
    if (summary.numbers || data.key || (data.thumbs || []).length) {
      faceStudy(env, body);
      return;
    }
    var strip = hasStrip(data);
    setFrame("study", {
      "data-strip": strip ? "1" : "0",
      "data-key": data.key ? "chart" : "thumb",
    });
    head(env, summary, data);
    body.appendChild(leadOf(summary, data));
    body.appendChild(keySlot(summary, data, savedState()));
    body.appendChild(tiles(summary));
    body.appendChild(footingOf(summary));
    if (strip) body.appendChild(stripOf(env, data));
  }

  /**
   * The run frame's body (13 §2.6, D-381 (2)): a served line (the queue
   * words, else the time words, else the progress words; never the chip's
   * own words again), the progress bar (geometry only), then the body box:
   * the partial key chart when served, else up to five steps with their
   * served words and "+N more steps", then the time line. A stopped card
   * says its served stopped words and draws the same box frozen.
   */
  function progressBody(env, data, body, opts) {
    var o = opts || {};
    var p = env.progress || {};
    var summary = env.card_summary || {};
    var chip = summary.state_words || "";
    body.setAttribute("data-run", o.stopped ? "stopped" : "running");
    if (document.body) document.body.setAttribute("data-no-expand", "1");
    var first = o.stopped
      ? p.receipt || p.stopped || p.words
      : p.queue || p.time_words || p.words;
    if (first && first === chip) first = p.words !== chip ? p.words : null;
    body.appendChild(el("p", "st-run-words", words(first)));
    if (!o.stopped) {
      var bar = el("div", "st-progress");
      var part = el("span", "st-progress-fill");
      var frac = p.children
        ? Math.max(0, Math.min(1, (p.done || 0) / p.children))
        : 0;
      part.style.width = frac * 100 + "%";
      bar.appendChild(part);
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", String(p.children || 0));
      bar.setAttribute("aria-valuenow", String(p.done || 0));
      if (p.words) bar.setAttribute("aria-valuetext", p.words);
      body.appendChild(bar);
    }
    var box = el("div", "st-run-box");
    var drew = false;
    if (data.key) {
      try {
        box.appendChild(chartBlock(data.key, data, { state: savedState() }));
        drew = true;
      } catch (e) {
        if (!e.thumb) throw e;
      }
    }
    var steps = data.steps || [];
    if (!drew && steps.length) {
      var list = el("ul", "st-steps");
      steps.slice(0, MAX_STEPS).forEach(function (st) {
        var li = el("li", "st-step");
        li.appendChild(el("span", "st-step-label", words(st.label)));
        li.appendChild(el("span", "st-step-words", words(st.words)));
        list.appendChild(li);
      });
      box.appendChild(list);
      var more = (data.copy || {}).more_steps;
      if (more) box.appendChild(el("p", "st-steps-more", more));
      drew = true;
    }
    if (!drew) box.setAttribute("data-empty", "1");
    body.appendChild(box);
    // The time words close the frame unless they already led it.
    var last = o.stopped || first === p.time_words ? null : p.time_words;
    body.appendChild(el("p", "st-run-time", words(last)));
  }

  /** A run in progress, or a finished run inside the wait (13 §2.6). */
  function renderRun(env, body) {
    var summary = env.card_summary || {};
    var data = cardDataOf(env);
    var p = env.progress || {};
    var state = stateOf(env);
    // D-354 (4): only a card that updates in place (queued, running) keeps the
    // reserved frame; a terminal one is as tall as what it says. D-381 (2):
    // the frame is the done card's height from the start, so a host whose
    // frame never grows (ChatGPT) clips nothing when the result lands.
    var terminal =
      state === "failed" ||
      state === "cancelled" ||
      state === "completed_partial";
    setFrame("run", terminal ? { "data-terminal": "1" } : null);
    head(env, summary, data);
    if (state === "completed" || state === "completed_partial") {
      body.setAttribute("data-run", "done");
      faceStudy(env, body, "face-run-done");
      return;
    }
    // Expand only once the run is done (13 §2.6).
    if (document.body) document.body.setAttribute("data-no-expand", "1");
    // a run that didn't finish is its run face, frozen: how far it got, what
    // failed (crossed dots), and its served words
    faceRun(env, body, {
      stopped: state === "cancelled" || state === "failed",
      failed: state === "failed",
    });
  }

  /**
   * The plan receipt (13 §2.6): card line, work words, count line; no Run.
   * D-381 (1): the count line and the wall are the card's served forms
   * (`card_allowance`, `card_limit`: no plan named, never a price); a plan
   * that may not run shows its wall IN the closed row, in place of the
   * work words. A saved Draft that never ran (13 §5, a `get`) is the same
   * row: its title, its served note chip and the Draft's run words.
   */
  function renderPlan(env, body) {
    var w = env.words || {};
    var steps = Array.isArray(env.steps) ? env.steps : [];
    var study = env.study || {};
    // MCP-CARDS finding 1: a saved Draft read by get shows its plan.
    if (study.draft && !steps.length) {
      facePlan(env, body);
      return;
    }
    var data = cardDataOf(env);
    var walled = env.run_allowed === false && w.card_limit;
    setFrame(null);
    // A plan answer's study carries no draft; a Draft read by get does.
    var draft = study.draft || null;
    var spec = {
      name: w.card_line || study.title || null,
      note: w.work || (draft && draft.words) || null,
      // The row's own line: the wall when the plan may not run, else the
      // count line; it wraps to two lines, never cut short.
      provenance: walled ? w.card_limit : w.card_allowance || null,
      url: (env.links || {}).app || null,
      wordEnd: true,
      keep: true,
    };
    if (draft)
      spec.chips = (data.chips || []).map(function (c) {
        return { text: c, tone: "muted" };
      });
    if (steps.length || w.queue || walled)
      spec.disclosure = {
        label: H.words && H.words.showSteps,
        build: function (into) {
          var list = el("ul", "st-steps st-plan-steps");
          steps.forEach(function (s) {
            var li = el("li", "st-step");
            li.appendChild(el("span", "st-step-label", words(s.label)));
            var why =
              (s.locked && (s.locked.words || s.locked.message)) ||
              (s.refused && (s.refused.words || s.refused.message)) ||
              s.words;
            if (why) li.appendChild(el("span", "st-step-words", words(why)));
            list.appendChild(li);
          });
          into.appendChild(list);
          if (w.queue) into.appendChild(el("p", "st-plan-w", w.queue));
          // The wall is in the closed row already (D-381 (1)): said once.
        },
      };
    body.appendChild(H.receiptRow(spec));
  }

  /** The list (13 §2.1): up to five studies, each opening its study. */
  function renderList(env, body) {
    var data = cardDataOf(env);
    if (data.head_words) {
      faceList(env, body);
      return;
    }
    // A list is for choosing (13 §2.1): nothing to expand.
    setFrame("list", { "data-no-expand": "1" });
    H.header({ name: data.head_words || null, untitled: false });
    var brand = document.querySelector("#card-head .brand");
    if (brand) brand.hidden = true;
    var rows = env.studies || [];
    var extra = data.rows || [];
    var list = el("ul", "st-list");
    rows.slice(0, MAX_LIST).forEach(function (r, i) {
      var x = extra[i] || {};
      var li = el("li", "st-list-row");
      var url = x.link || null;
      var b = el(url ? "a" : "span", "st-list-link");
      if (url) {
        b.href = url;
        b.addEventListener("click", function (ev) {
          ev.preventDefault();
          H.openLink(url);
        });
      }
      b.appendChild(el("span", "st-list-title", words(r.title)));
      // MCP-FINAL list-pro: at 360 the kind and state were hidden, so a
      // stopped map read as a bare title and two same-named studies read
      // alike. They share one meta line under the title there (inline above).
      var meta = el("span", "st-list-meta");
      if (r.kind_label && r.kind_label.short)
        meta.appendChild(el("span", "chip kind", r.kind_label.short));
      meta.appendChild(el("span", "st-list-state", words(r.state_words)));
      if (x.lead_number_label && x.lead_number_value) {
        // CARD-FINAL B4 (list at 360): the label gives way, the value stays
        // whole ("How well it ranks, next day –…" lost the number).
        var num = el("span", "st-list-num st-list-num-split");
        num.appendChild(el("span", "st-list-num-label", x.lead_number_label));
        num.appendChild(el("span", "st-list-num-value", x.lead_number_value));
        num.title = x.lead_number_words || "";
        meta.appendChild(num);
      } else if (x.lead_number_words)
        meta.appendChild(el("span", "st-list-num", x.lead_number_words));
      b.appendChild(meta);
      if (r.title) b.title = r.title;
      li.appendChild(b);
      list.appendChild(li);
    });
    // 13 §5's Empty list: the served "No studies yet", never a count over a
    // blank box (D-381 (2)).
    var empty = (data.copy || {}).no_studies;
    if (!rows.length && empty) {
      var li0 = el("li", "st-list-empty", empty);
      list.appendChild(li0);
    }
    body.appendChild(list);
    body.appendChild(el("p", "st-list-more", words(data.more_words)));
  }

  /** One chart's data read (13 §2.1): that chart at card size. */
  function renderChart(env, body) {
    var summary = env.card_summary || {};
    var data = cardDataOf(env);
    // MCP-CARDS (D-420): one chart's read on the face, at its content height
    faceFrame("chart");
    body.appendChild(faceHead(env, summary, data));
    var focus = data.focus || {};
    var entry = null;
    (data.charts || []).forEach(function (e) {
      if (!entry && e && e.index === focus.index) entry = e;
    });
    var chart = (entry && entry.chart) || data.key;
    // D-337 (1): the read view's chart; else its thumb under its own title
    // (the entry's, or the study's key thumb when it is that view); never a
    // bare header. With neither served, the slot still names the view.
    var thumb =
      (entry && entry.thumb) ||
      (summary.thumb &&
      focus.index != null &&
      summary.thumb.view_index === focus.index
        ? summary.thumb
        : null);
    var slot = el("div", "sf-chart st-key-slot");
    if (chart) {
      try {
        slot.appendChild(
          chartBlock(chart, data, {
            state: savedState(),
            onState: saveState,
            v: entry ? entry.v : null,
            hideTitle: (env.study || {}).title || summary.title || undefined,
            face: true,
          }),
        );
        slot.setAttribute("data-key", "chart");
      } catch (e) {
        if (!e.thumb) throw e;
        slot.setAttribute("data-chart-error", e.message);
      }
    } else {
      var named = el("div", "st-chart-title");
      named.appendChild(
        el("span", "st-chart-name", words((entry && entry.title) || "")),
      );
      slot.appendChild(named);
      if (!thumb || !drawThumbInto(slot, thumb, null, slot))
        slot.appendChild(el("div", "st-run-box"));
      slot.setAttribute("data-key", thumb ? "thumb" : "none");
    }
    body.appendChild(slot);
    if (focus.rows_words)
      body.appendChild(el("p", "sf-caption", words(focus.rows_words)));
  }

  /** Addresses, a project, a library read (D-328 D8): one served receipt row. */
  function renderReceipt(env, body, mode) {
    setFrame(null);
    var w = env.words || {};
    var url = mode === "project" ? (env.links || {}).app || null : null;
    if (!url && document.body) document.body.setAttribute("data-no-hero", "1");
    body.appendChild(
      H.receiptRow({ name: w.card_line || env.card_line || null, url: url }),
    );
  }

  /**
   * A refusal (13 §5; D-328 D3): the served card words (`detail.card_words`,
   * else the message's first line) in ordinary text, and one link: the study
   * or Research, as served in `detail.links.app` (the adapter's link row).
   * No price and no plan's name: nothing of that kind is composed here
   * (13 §7.4's wall).
   */
  function renderRefusal(env, body) {
    var detail = env.detail && typeof env.detail === "object" ? env.detail : {};
    var text =
      words(detail.card_words) ||
      String((H.prose ? H.prose(env.message) : env.message) || "").split(
        "\n",
      )[0];
    // CARD-RENDER refusal-validator: "Plan refused: 2 issues" said what, not
    // which. The validator's issues are served one by one (`detail.issues`:
    // its message and its suggestion, "Did you mean …" where it has one);
    // the card lists up to three, so it is as tall as what it says.
    var issues = (Array.isArray(detail.issues) ? detail.issues : []).filter(
      function (i) {
        return i && typeof i.message === "string" && i.message;
      },
    );
    // MCP-CARDS (D-420): a refusal is one row at its content height (the
    // house error pattern), no wordmark; its words are the served card form
    // (a plan limit's names no plan: D-328 D3, D-381; the model's text keeps
    // D-14's clause) and its one link is the served study or Research home.
    var extra = { "data-no-expand": "1" };
    if (issues.length) extra["data-terminal"] = "1";
    faceFrame("refusal", extra);
    var row = el("div", "st-refusal");
    row.appendChild(el("p", "st-refusal-w", text));
    body.appendChild(row);
    if (issues.length) {
      var ul = el("ul", "st-issues");
      issues.slice(0, MAX_ISSUES).forEach(function (i) {
        var li = el("li", "st-issue");
        li.appendChild(el("span", "st-issue-w", i.message));
        if (typeof i.suggestion === "string" && i.suggestion)
          li.appendChild(el("span", "st-issue-s", i.suggestion));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
    var link = detail.links && detail.links.app;
    if (!link && document.body) document.body.setAttribute("data-no-hero", "1");
  }

  /** Fullscreen (13 §2.3): header, lead; the tabs; then the numbers. */
  /**
   * MCP-CARDS (D-420d, founder: "the expand view can show more too, instead
   * of the same thing just bigger"): Expand is a dashboard. The face's head
   * and hero; selectors (Charts · Checks · Numbers) and Open in Keel up top;
   * the key chart large; every other view as a grid of small charts, all at
   * once (2-3 columns at 720 px and up, one on a phone); "Your checks" as
   * rows, each its mark, its words and its view's small chart; the numbers
   * with full labels and intervals. A tap on a chart focuses it (large, with
   * its readout, legend and its own Open in Keel) and "All charts" goes back.
   */
  function renderFull(env, body) {
    var summary = env.card_summary || {};
    var data = cardDataOf(env);
    // a study with nothing to show yet keeps its face in fullscreen too: in
    // flight or stopped empty its run face, a Draft its plan
    var st = stateOf(env);
    if (IN_FLIGHT[st] || st === "failed") {
      faceRun(env, body, { stopped: st === "failed", failed: st === "failed" });
      return;
    }
    if (st === "cancelled" && !data.key) {
      faceRun(env, body, { stopped: true });
      return;
    }
    if (
      (env.study || {}).draft &&
      !data.key &&
      !(summary.numbers || []).length
    ) {
      facePlan(env, body);
      return;
    }
    var copy = data.copy || {};
    var links = data.links || {};
    var notes = data.notes || {};
    var state = savedState();
    var url = (env.links && env.links.app) || env.hero_url;
    // the dashboard draws its one study link up top (no second link below)
    setFrame("full", { "data-face": "1", "data-owns-link": url ? "1" : "0" });
    var headEl = document.getElementById("card-head");
    if (headEl) headEl.hidden = true;
    if (typeof H.setOpenInAppUrl === "function") H.setOpenInAppUrl(url);
    var dash = el("div", "sf-dash");
    dash.appendChild(faceHead(env, summary, data));
    var face = servedFace(summary, data);
    if (face.hero) dash.appendChild(faceHero(face.hero, facePips(data)));
    if (notes.linked && notes.linked.words) {
      if (notes.linked.link)
        dash.appendChild(
          linkButton(
            notes.linked.words,
            notes.linked.link,
            "st-link st-link-small st-linked",
          ),
        );
      else dash.appendChild(el("p", "st-note st-linked", notes.linked.words));
    }
    var chartLink = function (e) {
      return e && e.v && links.chart ? fill(links.chart, { v: e.v }) : null;
    };
    var entries = (data.charts || []).filter(function (e) {
      return e && e.title;
    });
    var checksView = entries.filter(function (e) {
      return e.family === "Checks";
    })[0];
    var grid = entries.filter(function (e) {
      return e.family !== "Checks";
    });
    var rows = ((data.checks && data.checks.rows) || []).filter(function (r) {
      return r;
    });
    var numbers = (summary.numbers || []).filter(function (n) {
      return n && n.label;
    });
    var by = entriesByIndex(data);

    // ── one chart, focused ──
    var focus = el("div", "sf-focus");
    focus.hidden = true;
    var lastTile = null;
    var showFocus = function (e, from) {
      focus.textContent = "";
      lastTile = from || null;
      var back = el(
        "button",
        "button-link button-quiet sf-back",
        copy.all_charts || copy.charts,
      );
      back.type = "button";
      back.addEventListener("click", function () {
        showDash();
      });
      focus.appendChild(back);
      var panel = el("div", "sf-chart sf-focus-panel");
      panel.setAttribute("data-index", String(e.index));
      var chart = e.form === "chart" ? e.chart : null;
      if (chart) {
        try {
          panel.appendChild(
            chartBlock(chart, data, {
              state: state,
              onState: saveState,
              v: e.v,
              face: true,
            }),
          );
        } catch (err) {
          if (!err.thumb) throw err;
          panel.setAttribute("data-chart-error", err.message);
        }
      } else {
        panel.appendChild(el("h3", "sf-focus-t", words(e.title)));
        if (e.form === "thumb" && e.thumb)
          drawThumbInto(panel, e.thumb, null, panel);
        panel.appendChild(
          el("p", "st-note", words(e.state_words || copy.full_chart_in_keel)),
        );
      }
      focus.appendChild(panel);
      if (e.lead) focus.appendChild(el("p", "sf-focus-lead", words(e.lead)));
      var href = chartLink(e);
      if (href && copy.open_chart)
        focus.appendChild(
          linkButton(copy.open_chart, href, "st-link sf-open-chart"),
        );
      dash.hidden = true;
      focus.hidden = false;
      state.tab = "v" + e.index;
      saveState(state);
      if (window.scrollTo) window.scrollTo(0, 0);
      back.focus({ preventScroll: true });
    };
    var showDash = function () {
      focus.hidden = true;
      focus.textContent = "";
      dash.hidden = false;
      state.tab = null;
      saveState(state);
      if (lastTile) {
        if (lastTile.scrollIntoView)
          lastTile.scrollIntoView({ block: "center" });
        lastTile.focus({ preventScroll: true });
      }
    };

    // ── the selectors and the study link ──
    var sections = [];
    var bar = el("div", "sf-bar");
    var seg = el("nav", "sf-seg");
    bar.appendChild(seg);
    if (url) {
      var open = linkButton(
        H.studyLinkLabel ? H.studyLinkLabel(url) : copy.open_chart,
        url,
        "button-link button-primary sf-open",
      );
      bar.appendChild(open);
    }
    dash.appendChild(bar);
    var section = function (id, label, title) {
      var sec = el("section", "sf-sec");
      sec.id = "sf-" + id;
      if (title) sec.appendChild(el("h3", "sf-sec-h", words(title)));
      sections.push({ id: id, label: label, el: sec });
      return sec;
    };

    // ── Charts: the key large, then the grid ──
    if (data.key || grid.length) {
      var charts = section("charts", copy.seg_charts || copy.charts, null);
      if (data.key) {
        var key = el("div", "sf-chart sf-key st-key-slot");
        try {
          key.appendChild(
            chartBlock(data.key, data, {
              state: state,
              onState: saveState,
              face: true,
            }),
          );
          key.setAttribute("data-key", "chart");
        } catch (err) {
          if (!err.thumb) throw err;
          key.setAttribute("data-chart-error", err.message);
        }
        charts.appendChild(key);
        var keyHref = data.key_v ? chartLink({ v: data.key_v }) : null;
        if (keyHref && copy.open_chart)
          charts.appendChild(
            linkButton(copy.open_chart, keyHref, "st-link sf-open-chart"),
          );
      }
      if (grid.length) {
        var g = el("div", "sf-grid");
        grid.forEach(function (e) {
          var tile = el("div", "sf-gtile");
          tile.setAttribute("data-index", String(e.index));
          var hit = el("button", "sf-gtile-hit");
          hit.type = "button";
          hit.title = e.title;
          hit.appendChild(el("span", "sf-gtile-t", words(e.title)));
          var pic = el("span", "sf-gtile-pic");
          var drawn = e.form === "chart" ? miniChart(e.chart, "sf-mini") : null;
          if (drawn) {
            pic.appendChild(drawn);
            tile.setAttribute("data-drawn", "chart");
          } else if (
            e.form === "chart" &&
            e.chart &&
            e.chart.kind === "table" &&
            (e.chart.rows || []).length
          ) {
            pic.appendChild(miniTable(e.chart));
            tile.setAttribute("data-drawn", "table");
          } else if (e.form === "thumb" && e.thumb) {
            drawThumbInto(pic, e.thumb, null, hit);
            tile.setAttribute("data-drawn", "thumb");
          } else {
            pic.appendChild(
              el(
                "span",
                "sf-gtile-w",
                words(
                  e.state_words ||
                    (e.chart && (e.chart.note_words || e.chart.more_words)) ||
                    copy.full_chart_in_keel,
                ),
              ),
            );
            tile.setAttribute("data-drawn", "words");
          }
          hit.appendChild(pic);
          if (e.lead)
            hit.appendChild(el("span", "sf-gtile-lead", words(e.lead)));
          hit.addEventListener("click", function () {
            showFocus(e, hit);
          });
          tile.appendChild(hit);
          var href = chartLink(e);
          if (href && copy.open_chart) {
            var a = el("a", "sf-gtile-open", "↗");
            a.href = href;
            a.title = copy.open_chart;
            a.setAttribute("aria-label", copy.open_chart + ": " + e.title);
            a.addEventListener("click", function (ev) {
              ev.preventDefault();
              H.openLink(href);
            });
            tile.appendChild(a);
          }
          g.appendChild(tile);
        });
        charts.appendChild(g);
      }
      dash.appendChild(charts);
    }

    // ── Checks: one row a bar, its mark, its words, its view's small chart ──
    if (rows.length) {
      var checks = section(
        "checks",
        copy.seg_checks,
        checksView ? checksView.title : copy.seg_checks,
      );
      var plain = noBars(data);
      var list = el("ul", "sf-crows");
      var rank = function (r) {
        return r.locked ? 3 : r.met === true ? 0 : r.met === false ? 1 : 2;
      };
      rows
        .map(function (r, k) {
          return { r: r, k: k };
        })
        .sort(function (a, b) {
          return plain ? a.k - b.k : rank(a.r) - rank(b.r) || a.k - b.k;
        })
        .forEach(function (x) {
          var r = x.r;
          var li = el("li", "sf-crow");
          var st = checkState(r, plain);
          li.setAttribute("data-state", st);
          if (st !== "plain" && st !== "locked") {
            var pip = el("span", "sf-pip sf-crow-pip");
            pip.setAttribute(
              "data-state",
              st === "not_measured" ? "unmeasured" : st,
            );
            pip.setAttribute("aria-label", words(copy[st]));
            pip.title = words(copy[st]);
            li.appendChild(pip);
          }
          var text = el("div", "sf-crow-text");
          text.appendChild(el("span", "sf-crow-l", words(r.label)));
          var bits = r.locked
            ? [copy.locked]
            : [r.bar_words, r.interval_words, r.sample_words];
          bits = bits.filter(function (w) {
            return typeof w === "string" && w;
          });
          if (bits.length)
            text.appendChild(el("span", "sf-crow-sub", bits.join(SEP)));
          li.appendChild(text);
          if (!r.locked)
            li.appendChild(el("span", "sf-crow-v", words(r.value_words)));
          var v = typeof r.view === "number" ? by[r.view] : null;
          var mini =
            v && v.form === "chart" ? miniChart(v.chart, "sf-mini") : null;
          if (mini) {
            var mb = el("button", "sf-crow-chart");
            mb.type = "button";
            mb.title = v.title;
            mb.setAttribute("aria-label", v.title);
            mb.setAttribute("data-index", String(v.index));
            mb.appendChild(mini);
            mb.addEventListener("click", function () {
              showFocus(v, mb);
            });
            li.appendChild(mb);
          } else {
            // the chart's column stays, so every row's value lines up
            var none = el("span", "sf-crow-none");
            none.setAttribute("aria-hidden", "true");
            li.appendChild(none);
          }
          list.appendChild(li);
        });
      checks.appendChild(list);
      if (data.checks && data.checks.more_words)
        checks.appendChild(
          el("p", "sf-caption", words(data.checks.more_words)),
        );
      dash.appendChild(checks);
    }

    // ── Numbers: full labels, values, intervals and samples ──
    if (numbers.length) {
      var nums = section("numbers", copy.seg_numbers, copy.numbers);
      numbers.forEach(function (n) {
        var row = el("div", "st-num-row");
        row.appendChild(el("span", "st-num-label", words(n.label)));
        row.appendChild(
          el("span", "st-num-value st-value", words(n.value_words)),
        );
        var sub = [n.interval_words, n.sample_words].filter(function (x) {
          return typeof x === "string" && x;
        });
        if (sub.length)
          row.appendChild(el("span", "st-num-sub", sub.join(SEP)));
        nums.appendChild(row);
      });
      dash.appendChild(nums);
    }

    // the footing and the notes close the dashboard
    var foot = el("div", "sf-foot");
    if (summary.footing_line)
      foot.appendChild(el("p", "sf-caption", words(summary.footing_line)));
    [notes.draft, notes.saved]
      .concat(notes.not_measured || [], notes.stale || [])
      .forEach(function (w) {
        if (typeof w === "string" && w) foot.appendChild(el("p", "st-note", w));
      });
    if (foot.children.length) dash.appendChild(foot);

    // the selectors jump to their section; the one in view is marked
    sections.forEach(function (sec) {
      if (!sec.label) return;
      var b = el("button", "sf-seg-b", sec.label);
      b.type = "button";
      b.setAttribute("aria-controls", sec.el.id);
      b.addEventListener("click", function () {
        if (sec.el.scrollIntoView)
          sec.el.scrollIntoView({ block: "start", behavior: "smooth" });
        mark(sec.id);
      });
      sec.button = b;
      seg.appendChild(b);
    });
    var mark = function (id) {
      sections.forEach(function (sec) {
        if (sec.button)
          sec.button.setAttribute(
            "aria-current",
            sec.id === id ? "true" : "false",
          );
      });
    };
    if (sections.length) mark(sections[0].id);
    if (sections.length < 2) seg.hidden = true;
    // the section whose top has passed the bar is the one in view (the first
    // at the top of the page)
    if (sections.length > 1 && window.addEventListener) {
      var spy = function () {
        if (dash.hidden) return;
        var at = sections[0].id;
        sections.forEach(function (sec) {
          if (sec.el.getBoundingClientRect().top <= 96) at = sec.id;
        });
        mark(at);
      };
      window.addEventListener("scroll", spy, { passive: true });
    }

    body.appendChild(dash);
    body.appendChild(focus);
    // a thumbnail tapped inline opens Expand on its chart
    var asked = /^v(\d+)$/.exec(String(state.tab || ""));
    if (asked && by[Number(asked[1])]) {
      var tile = dash.querySelector(
        '.sf-gtile[data-index="' + asked[1] + '"] .sf-gtile-hit',
      );
      showFocus(by[Number(asked[1])], tile);
    }
  }

  /** An older card for the same study: its receipt row; "Show" reopens it (13 §4.4). */
  function renderSuperseded(env, body) {
    var summary = env.card_summary || {};
    var study = env.study || {};
    setFrame(null);
    body.appendChild(
      H.receiptRow({
        name: study.title || summary.title || null,
        version: summary.version != null ? summary.version : study.version,
        state: summary.state_words || null,
        superseded: H.supersededLabel ? H.supersededLabel() : null,
        url: (env.links && env.links.app) || env.hero_url || null,
        wordEnd: true,
        disclosure: {
          label: H.words && H.words.showStudy,
          build: function (into) {
            var data = cardDataOf(env);
            into.setAttribute("data-layout", "study");
            into.appendChild(leadOf(summary, data));
            into.appendChild(keySlot(summary, data, savedState()));
            into.appendChild(tiles(summary));
            into.appendChild(footingOf(summary));
          },
        },
      }),
    );
  }

  /** `view.mode` as the SDK serves it; an envelope without one is a v1 server's. */
  function modeOf(env) {
    var view = env.view || {};
    if (typeof view.mode === "string" && view.mode) return view.mode;
    if (view.size === "receipt") return "legacy-receipt";
    return "study";
  }

  function render(env) {
    var body = document.getElementById("card-body");
    if (!body) return;
    applyPalette();
    body.textContent = "";
    body.removeAttribute("aria-busy");
    body.removeAttribute("data-run");
    lastMode = H.displayMode();
    lastNarrow = H.isNarrow();
    if (env.code && env.message && !env.view) {
      renderRefusal(env, body);
    } else {
      var mode = modeOf(env);
      var full = lastMode === "fullscreen";
      if (
        superseded &&
        !full &&
        (mode === "study" || mode === "run" || mode === "plan")
      )
        renderSuperseded(env, body);
      else if (full && (mode === "study" || mode === "run" || mode === "chart"))
        renderFull(env, body);
      else if (mode === "run") renderRun(env, body);
      else if (mode === "plan") renderPlan(env, body);
      else if (mode === "list") renderList(env, body);
      else if (mode === "chart") renderChart(env, body);
      else if (mode === "addresses" || mode === "project" || mode === "library")
        renderReceipt(env, body, mode);
      else if (mode === "legacy-receipt") {
        var summary = env.card_summary || {};
        setFrame(null);
        head(env, summary, {});
        body.appendChild(
          el(
            "p",
            "st-receipt",
            words((env.progress && env.progress.words) || summary.state_words),
          ),
        );
        if (document.body) document.body.setAttribute("data-size", "receipt");
      } else renderStudy(env, body);
    }
    if (typeof H.refreshLinkRow === "function") H.refreshLinkRow();
    if (typeof H.notifySize === "function") H.notifySize();
  }

  H.ready(function (env) {
    lastEnv = env || {};
    superseded = false;
    render(lastEnv);
  });

  if (typeof H.onSuperseded === "function") {
    H.onSuperseded(function () {
      superseded = true;
      if (lastEnv) render(lastEnv);
    });
  }

  // Registered once: a display-mode change or a crossing of the 480 px band
  // redraws; any other resize repaints nothing (13 §4.2).
  if (typeof H.onResize === "function") {
    H.onResize(function () {
      if (!lastEnv) return;
      if (H.displayMode() !== lastMode || H.isNarrow() !== lastNarrow)
        render(lastEnv);
    });
  }
})();
