/* Backtest comparison card — renders keel_backtest_compare's envelope
 * (BUILD §2.2 for the contract, §4.4 for the behaviour; Q-1688).
 *
 * Two to eight runs of one strategy, or runs that span strategies. The
 * card answers ONE question — which of these is better, and at what
 * cost — so it draws the numbers that differ, the curves overlaid, and
 * nothing that merely repeats the header.
 *
 * Density is the whole design problem here. Claude's own guidance caps
 * an inline card at "4-5 data points"; mobile hosts clip inline height
 * without scrolling. So:
 *
 *   ≤ 4 runs, wide     one column per run, four rows, `+3 more` in place
 *   > 4 runs, wide     one LINE per run (label · Sharpe · Return)
 *   under 480 px       one block per run, four numbers, capped at 4
 *   fullscreen         every row, the deltas, the tearsheet links
 *
 * Every collapse names its remainder, which is the card system's rule
 * and the only thing that makes a cap honest.
 *
 * Deltas are NOT drawn inline. They are in the envelope for the model
 * (`view.deltas`), and inline they would double the table's height to
 * restate a subtraction the reader can do. Fullscreen draws them as a
 * muted sub-line, where there is room.
 *
 * Colour is never the sole carrier: the baseline is the heaviest line
 * AND the ink AND the column marked `baseline`; the legend repeats
 * every label beside its own swatch; runs beyond six reuse the six
 * hues DASHED.
 */
(function () {
  "use strict";
  var H = window.KeelHost;
  var F = H.fmt;
  var el = H.el;
  var DASH = F.DASH;
  var SVG_NS = "http://www.w3.org/2000/svg";

  // The series order is decided (review page, 2026-09-22; Q-1782): the
  // INK for the baseline — heaviest, neutral, never mistaken for a run —
  // then cyan · amber · green · rose · violet, violet last. The accent
  // was the baseline until Q-1782 and reads violet in both themes, which
  // put "violet first". Runs beyond six reuse the sequence dashed.
  var SERIES = [
    { stroke: "var(--fg)", base: true },
    { stroke: "var(--sv-cyan)" },
    { stroke: "var(--sv-amber)" },
    { stroke: "var(--sv-green)" },
    { stroke: "var(--sv-rose)" },
    { stroke: "var(--sv-violet)" },
  ];
  function seriesFor(i) {
    var s = SERIES[i % SERIES.length];
    return {
      stroke: s.stroke,
      base: i === 0,
      dash: i >= SERIES.length ? "5 4" : null,
    };
  }

  // The metric rows, in the SERVER's order and words (`view.rows`, each
  // `{key, label}` — Q-1746, Q-1787). The first four are inline (the app's
  // headline four: Return · Max drawdown · Sharpe · Win rate); the next
  // three are what `+3 more` reveals; the rest are fullscreen. The card
  // owns only how each KEY is read and formatted (`FORMAT`).
  var FORMAT = {
    total_return_pct: { f: F.pct, dp: 1, unit: " pts" },
    max_drawdown_pct: { f: F.drawdown, dp: 1, unit: " pts" },
    sharpe: { f: F.ratio, dp: 2, unit: "" },
    win_rate_pct: { f: F.rate, dp: 1, unit: " pts" },
    position_win_rate_pct: { f: F.rate, dp: 1, unit: " pts" },
    // The run count in envelopes before 2026-09-25 (before Q-1746, `fills`);
    // newer rows name their format instead (`KINDS`).
    round_trips: {
      f: F.count,
      dp: 0,
      unit: "",
      keys: ["round_trips", "fills"],
    },
    fees_paid: {
      f: function (v) {
        return F.price(v, { dp: 0 });
      },
      dp: 0,
      unit: "",
      money: true,
    },
    turnover: {
      f: function (v) {
        return v == null || isNaN(Number(v))
          ? F.text(null)
          : { text: Math.round(Number(v)) + "x", tone: "" };
      },
      dp: 0,
      unit: "x",
    },
    sortino: { f: F.ratio, dp: 2, unit: "" },
    calmar: { f: F.ratio, dp: 2, unit: "" },
    profit_factor: { f: F.ratio, dp: 2, unit: "" },
    position_profit_factor: { f: F.ratio, dp: 2, unit: "" },
  };
  // A row the server marks with a format kind (`format: "count"`) needs no
  // entry by key here.
  var KINDS = { count: { f: F.count, dp: 0, unit: "" } };
  // How a key the card has no rule for is drawn: a plain two-place number.
  var GENERIC = { f: F.ratio, dp: 2, unit: "" };

  // ONLY for an envelope built before the server served every row: the
  // same keys, order and words `build_comparison_view` serves today.
  // Never a second set of labels.
  var LEGACY_ROWS = [
    ["total_return_pct", "Return"],
    ["max_drawdown_pct", "Max drawdown", "Max DD"],
    ["sharpe", "Sharpe"],
    ["win_rate_pct", "Win rate"],
    // No count row (Q-1906): its label is served only — see
    // card-backtest.js `LEGACY_TILES`.
    ["turnover", "Turnover (× capital)", "Turnover"],
    ["sortino", "Sortino"],
    ["calmar", "Calmar"],
    ["profit_factor", "Profit factor"],
    ["fees_paid", "Fees paid"],
  ];

  function rowSpec(key, label, shortLabel, format) {
    return Object.assign(
      {},
      (format && KINDS[format]) || FORMAT[key] || GENERIC,
      {
        key: key,
        label: label,
        // The server's short form (Q-1799): what a narrow header draws.
        short_label: shortLabel || null,
      },
    );
  }

  /** The rows to draw: the server's `view.rows`, else the legacy list. */
  function rowsFor(v) {
    var served = v && Array.isArray(v.rows) ? v.rows : [];
    if (served.length) {
      return served
        .filter(function (r) {
          return r && typeof r.key === "string" && typeof r.label === "string";
        })
        .map(function (r) {
          return rowSpec(r.key, r.label, r.short_label, r.format);
        });
    }
    return LEGACY_ROWS.map(function (r) {
      return rowSpec(r[0], r[1], r[2]);
    });
  }
  var INLINE_ROWS = 4;
  var REVEALED_ROWS = 7; // what `+3 more` grows the table to
  var INLINE_RUNS = 4;
  var INLINE_LINES = 4; // curves drawn inline

  /** A delta beside a number: signed, or `±` when it rounds to zero. */
  function deltaText(v, dp, unit, money) {
    if (v == null || isNaN(Number(v))) return DASH;
    var n = Number(v);
    var body = Math.abs(n).toFixed(dp);
    if (money) body = body.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    var sign = parseFloat(body.replace(/,/g, ""))
      ? n > 0
        ? "+"
        : F.MINUS
      : "±";
    return sign + (money ? "$" : "") + body + (money ? "" : unit);
  }

  function windowText(w) {
    if (!w) return null;
    var span = H.fmtWindow(w); // ends on the last covered day (spec 03 §2.5)
    var chip = F.age(w.start, w.end);
    if (span && chip) {
      var tail = " · " + chip;
      if (span.length > tail.length && span.slice(-tail.length) === tail) {
        span = span.slice(0, -tail.length);
      }
    }
    return span;
  }

  /**
   * Labels that tell the runs apart (Q-1780). Three strategies named
   * "Simple Mean Reversion (Top 20 Perps, 20D)" / "…10D)" / "…30D)"
   * rendered as three columns headed "Simple Mean Reversion (To…" — the
   * part that differs was the part cut. When every label shares a long
   * prefix (cut back to a word boundary), each run is labelled by what is
   * LEFT ("20D"), and the shared stem is said once, in the header. A
   * closing bracket the stem opened is dropped from each remainder.
   * Returns `{stem, labels}`; `stem` is null when nothing is shared.
   */
  var STEM_MIN = 8;
  function distinctLabels(labels) {
    var none = { stem: null, labels: labels.slice() };
    if (labels.length < 2) return none;
    var p = labels[0];
    for (var i = 1; i < labels.length; i++) {
      var s = labels[i];
      var k = 0;
      while (k < p.length && k < s.length && p[k] === s[k]) k++;
      p = p.slice(0, k);
    }
    // Back to a boundary: never split a word or a number.
    var cut = p.search(/[\s(,·:–-][^\s(,·:–-]*$/);
    if (p.length < labels[0].length && cut >= 0) p = p.slice(0, cut + 1);
    if (p.replace(/\s+$/, "").length < STEM_MIN) return none;
    var opens = (p.match(/\(/g) || []).length - (p.match(/\)/g) || []).length;
    var rest = labels.map(function (l) {
      var r = l.slice(p.length).replace(/^[\s,·:–-]+/, "");
      if (opens > 0) r = r.replace(/\)\s*$/, "");
      return r;
    });
    for (var j = 0; j < rest.length; j++) if (!rest[j]) return none;
    var stem = p.replace(/[\s,·:–-]+$/, "");
    return {
      stem: opens > 0 ? stem + ", …)" : stem + " …",
      labels: rest,
    };
  }

  /** The label a run is SHOWN by: its distinguishing part (Q-1780). */
  function labelOf(r) {
    return r && r._shown ? r._shown : (r && r.label) || "";
  }

  /** A row's value in `obj`, under its key or an older spelling. */
  function pickKey(obj, spec) {
    var o = obj || {};
    var keys = spec.keys || [spec.key];
    for (var i = 0; i < keys.length; i++)
      if (o[keys[i]] != null) return o[keys[i]];
    return null;
  }

  function metric(run, spec) {
    return H.display(spec.f(pickKey(run.metrics, spec)));
  }

  /**
   * The table. `full` draws every row, the delta sub-lines, the
   * per-run tearsheet anchors and the spec-diff summary.
   *
   * `H.table` is the card system's table helper and it is deliberately
   * not used here: it maps one ROW per record, and this table is
   * transposed — one COLUMN per run, one row per metric. The narrow
   * form it would give for free is drawn explicitly below instead
   * (`blocks`), which is what §4.4 asks for anyway.
   */
  function table(v, full) {
    var ROWS = rowsFor(v); // the server's rows (Q-1787)
    var runs = v.runs;
    var host = el("div", "table-host");
    var t = el("table", "cmp");
    var head = el("tr");
    head.appendChild(el("th", "k", ""));
    runs.forEach(function (r, i) {
      var th = el("th", "run", labelOf(r));
      th.setAttribute("scope", "col");
      th.title = r.label;
      if (i === v.baseline) th.appendChild(el("span", "base", "baseline"));
      head.appendChild(th);
    });
    t.appendChild(head);

    function rowFor(spec) {
      var tr = el("tr");
      tr.appendChild(el("td", "k", spec.label));
      runs.forEach(function (r, i) {
        var d = metric(r, spec);
        var td = el("td", "num" + (d.tone ? " " + d.tone : ""), d.text);
        if (full && i !== v.baseline && v.deltas && v.deltas[i]) {
          td.appendChild(
            el(
              "span",
              "delta",
              deltaText(
                pickKey(v.deltas[i], spec),
                spec.dp,
                spec.unit,
                spec.money,
              ),
            ),
          );
        }
        tr.appendChild(td);
      });
      return tr;
    }

    var shown = full ? ROWS : ROWS.slice(0, INLINE_ROWS);
    shown.forEach(function (spec) {
      t.appendChild(rowFor(spec));
    });

    if (full) {
      var links = el("tr");
      links.appendChild(el("td", "k", "Tearsheet"));
      runs.forEach(function (r) {
        var td = el("td", "num");
        if (r.url) {
          // Real anchors with href + title: the host's open-link modal
          // is bypassed only after a real gesture on a destination the
          // reader can read before they press it.
          var a = el("a", null, "Open ↗");
          a.href = r.url;
          a.title = r.url;
          a.addEventListener("click", function (ev) {
            ev.preventDefault();
            H.openLink(r.url);
          });
          td.appendChild(a);
        } else {
          td.textContent = DASH;
        }
        links.appendChild(td);
      });
      t.appendChild(links);

      var diffs = el("tr");
      diffs.appendChild(el("td", "k", "Differs from baseline"));
      runs.forEach(function (r, i) {
        diffs.appendChild(
          el("td", "diff", i === v.baseline ? DASH : r.diff || DASH),
        );
      });
      t.appendChild(diffs);
    }
    host.appendChild(t);

    if (!full && ROWS.length > INLINE_ROWS) {
      var hidden = Math.min(REVEALED_ROWS, ROWS.length) - INLINE_ROWS;
      var more = el("button", "more", "+" + hidden + " more");
      more.type = "button";
      more.setAttribute("aria-expanded", "false");
      more.addEventListener("click", function (ev) {
        ev.preventDefault();
        ROWS.slice(INLINE_ROWS, REVEALED_ROWS).forEach(function (spec) {
          t.appendChild(rowFor(spec));
        });
        if (more.parentNode) more.parentNode.removeChild(more);
        H.notifySize();
      });
      host.appendChild(more);
    }
    return host;
  }

  /** > 4 runs, wide: one line per run, and the rest named. */
  function lines(v) {
    var ROWS = rowsFor(v); // the server's rows (Q-1787)
    var host = el("div", "row-list cmp-lines");
    v.runs.forEach(function (r, i) {
      var item = el("div", "row-item");
      var lead = el("span", "lead", labelOf(r));
      if (i === v.baseline) lead.appendChild(el("span", "base", "baseline"));
      item.appendChild(lead);
      ROWS.slice(0, 2).forEach(function (spec) {
        var d = metric(r, spec);
        var f = el("span", "field" + (d.tone ? " " + d.tone : ""));
        f.appendChild(el("span", "k", spec.label));
        f.appendChild(el("span", "v", d.text));
        item.appendChild(f);
      });
      host.appendChild(item);
    });
    host.appendChild(
      el("p", "note", "+" + (ROWS.length - 2) + " more columns in fullscreen"),
    );
    return host;
  }

  /** Under 480 px: one block per run, four numbers, capped at four. */
  function blocks(v) {
    var ROWS = rowsFor(v); // the server's rows (Q-1787)
    // The metric names ONCE, as a header row over aligned columns
    // (Q-1784): repeating SHARPE / RETURN / MAX DD / FILLS in every block
    // doubled each block's height and made four runs read as a wall of
    // labels. Each run is its name, then its four numbers under the header.
    var host = el("div", "row-list cmp-blocks");
    var cols = ROWS.slice(0, INLINE_ROWS);
    var head = el("div", "row-head");
    head.setAttribute("aria-hidden", "true");
    cols.forEach(function (spec) {
      // Under 480 px every header is narrow: the short form where the
      // server gives one (Q-1799), the full word as its tooltip.
      var k = el("span", "k", spec.short_label || spec.label);
      if (spec.short_label) k.title = spec.label;
      head.appendChild(k);
    });
    host.appendChild(head);
    var shown = v.runs.slice(0, INLINE_RUNS);
    shown.forEach(function (r, i) {
      var item = el("div", "row-item");
      var lead = el("span", "lead", labelOf(r));
      if (i === v.baseline) lead.appendChild(el("span", "base", "baseline"));
      item.appendChild(lead);
      cols.forEach(function (spec) {
        var d = metric(r, spec);
        var f = el("span", "field" + (d.tone ? " " + d.tone : ""));
        // The label stays for a screen reader, which has no header row.
        f.setAttribute("aria-label", spec.label + " " + d.text);
        f.appendChild(el("span", "v", d.text));
        item.appendChild(f);
      });
      host.appendChild(item);
    });
    if (v.runs.length > shown.length) {
      host.appendChild(
        el(
          "p",
          "note",
          "+" + (v.runs.length - shown.length) + " more in fullscreen",
        ),
      );
    }
    return host;
  }

  // ── The overlaid chart ────────────────────────────────────────────
  function svg(tag, attrs, cls) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        node.setAttribute(k, attrs[k]);
      });
    }
    if (cls) node.setAttribute("class", cls);
    return node;
  }

  function text(x, y, str, attrs, cls) {
    var t = svg("text", attrs, cls);
    t.setAttribute("x", x);
    t.setAttribute("y", y);
    t.textContent = str;
    return t;
  }

  function readCurve(curve) {
    if (!curve || !Array.isArray(curve.points)) return null;
    var pts = [];
    curve.points.forEach(function (p) {
      var t, eq, dd;
      if (Array.isArray(p)) {
        t = p[0];
        eq = p[1];
        dd = p[2];
      } else if (p && typeof p === "object") {
        t = p.t;
        eq = p.equity;
        dd = p.drawdown_pct;
      }
      var d = H.parseDate(t);
      if (!d || eq == null || isNaN(Number(eq))) return;
      pts.push({
        t: d.getTime(),
        equity: Number(eq),
        dd: dd == null || isNaN(Number(dd)) ? 0 : Number(dd),
      });
    });
    if (pts.length < 2) return null;
    pts.sort(function (a, b) {
      return a.t - b.t;
    });
    return pts;
  }

  function ticks(lo, hi, count) {
    if (!(hi > lo)) return [lo];
    var raw = (hi - lo) / count;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag;
    var step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
      out.push(Math.round(v / step) * step);
    }
    return out.length ? out : [lo, hi];
  }

  /**
   * The common window the server computed (`view.overlap`, Q-1802) as
   * epoch ms, or null. The card never derives it from the curves: the
   * server owns the window arithmetic, the card only draws it.
   */
  function overlapOf(v) {
    var o = v && v.overlap;
    if (!o || typeof o !== "object") return null;
    var a = H.parseDate(o.start);
    var b = H.parseDate(o.end);
    if (!a || !b || !(b.getTime() > a.getTime())) return null;
    return { t0: a.getTime(), t1: b.getTime(), severity: o.severity };
  }

  /**
   * One x-axis for every series, so windows that differ LOOK different:
   * a run that starts five months later starts five months in. When the
   * windows differ, the dates outside the COMMON window are shaded
   * (Q-1802): every curve stays whole, and the clear band is the period
   * every run covers.
   */
  function drawMulti(container, series, opts, width) {
    container.textContent = "";
    if (!series.length) return;
    var W = Math.max(240, Math.floor(width || container.clientWidth || 600));
    var tall = !!opts.tall;
    var padL = 44;
    var padR = 10;
    var padT = 10;
    var gap = 14;
    var padB = 18;
    var eqH = tall ? 280 : W < 420 ? 130 : 170;
    var ddH = opts.dd ? (tall ? 96 : W < 420 ? 48 : 60) : 0;
    var Hgt = padT + eqH + (opts.dd ? gap + ddH : 0) + padB;
    var plotW = W - padL - padR;

    var t0 = Infinity;
    var t1 = -Infinity;
    var lo = Infinity;
    var hi = -Infinity;
    var ddMin = 0;
    series.forEach(function (s) {
      s.pts.forEach(function (p) {
        if (p.t < t0) t0 = p.t;
        if (p.t > t1) t1 = p.t;
        if (p.equity < lo) lo = p.equity;
        if (p.equity > hi) hi = p.equity;
        if (p.dd < ddMin) ddMin = p.dd;
      });
    });
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    var pad = (hi - lo) * 0.06;
    var yLo = lo - pad;
    var yHi = hi + pad;
    if (ddMin > -0.5) ddMin = -0.5;

    function X(t) {
      return padL + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * plotW);
    }
    function Y(v) {
      return padT + eqH - ((v - yLo) / (yHi - yLo)) * eqH;
    }
    var ddTop = padT + eqH + gap;
    function YD(d) {
      return ddTop + (d / ddMin) * ddH;
    }

    // The shaded spans: union minus the common window, in plot pixels.
    var shade = [];
    var ov = opts.overlap;
    if (ov && t1 > t0) {
      var a = Math.max(t0, Math.min(t1, ov.t0));
      var b = Math.max(t0, Math.min(t1, ov.t1));
      if (a > t0) shade.push([t0, a]);
      if (b < t1) shade.push([b, t1]);
    }
    var root = svg("svg", {
      viewBox: "0 0 " + W + " " + Hgt,
      width: "100%",
      height: Hgt,
      role: "img",
      // The label carries the READING, not the chart's name (Q-1633).
      "aria-label":
        series.length +
        " equity curves overlaid over " +
        H.fmtPeriod(new Date(t0), new Date(t1)) +
        ". " +
        series[0].label +
        " is the baseline and is drawn heaviest." +
        (shade.length
          ? " Shaded: dates not every run covers; every run covers " +
            H.fmtPeriod(new Date(ov.t0), new Date(ov.t1)) +
            "."
          : ""),
    });
    var paneH = eqH + (opts.dd ? gap + ddH : 0);
    shade.forEach(function (span) {
      var x0 = X(span[0]);
      var w = Math.max(1, X(span[1]) - x0);
      root.appendChild(
        svg(
          "rect",
          { x: x0.toFixed(1), y: padT, width: w.toFixed(1), height: paneH },
          "outside",
        ),
      );
    });
    var grid = svg("g", null, "grid");
    var axis = svg("g", null, "axis");
    // One format for the whole value axis (Q-1802): 5k · 10k · 15k.
    var yt = ticks(yLo, yHi, 3).filter(function (v) {
      var y = Y(v);
      return y >= padT - 1 && y <= padT + eqH + 1;
    });
    var yl = H.fmtAxisValues(yt);
    yt.forEach(function (v, j) {
      var y = Y(v);
      grid.appendChild(svg("line", { x1: padL, x2: W - padR, y1: y, y2: y }));
      axis.appendChild(
        text(padL - 6, y + 3.5, yl[j], { "text-anchor": "end" }),
      );
    });
    if (opts.dd) {
      grid.appendChild(
        svg("line", { x1: padL, x2: W - padR, y1: ddTop, y2: ddTop }),
      );
      axis.appendChild(
        text(padL - 6, ddTop + 3.5, "0%", { "text-anchor": "end" }),
      );
      axis.appendChild(
        text(padL - 6, ddTop + ddH + 3.5, F.drawdown(ddMin, { dp: 0 }).text, {
          "text-anchor": "end",
        }),
      );
      axis.appendChild(
        text(
          W - padR,
          ddTop + 11,
          "drawdown",
          { "text-anchor": "end" },
          "pane-label",
        ),
      );
    }
    var n = W < 420 ? 3 : 4;
    var xt = [];
    for (var i = 0; i < n; i++) xt.push(t0 + (t1 - t0) * (i / (n - 1)));
    // One format for the whole axis (Q-1783): "Aug 2024 · Apr 2025 ·
    // Jan 2026 · Sep 2026", never "Aug 9, 2024 · Apr 24 · Jan 6".
    var xl = H.fmtAxisDates(xt);
    xt.forEach(function (t, j) {
      axis.appendChild(
        text(X(t), Hgt - 4, xl[j], {
          "text-anchor": j === 0 ? "start" : j === n - 1 ? "end" : "middle",
        }),
      );
    });
    root.appendChild(grid);
    root.appendChild(axis);

    // Drawn back to front so the baseline — the heaviest line and the
    // one every delta is against — is on top.
    series
      .slice()
      .reverse()
      .forEach(function (s) {
        var d = "";
        var dd = "";
        s.pts.forEach(function (p, i) {
          var x = X(p.t).toFixed(1);
          d += (i ? "L" : "M") + x + "," + Y(p.equity).toFixed(1);
          dd += (i ? "L" : "M") + x + "," + YD(p.dd).toFixed(1);
        });
        var attrs = { d: d, stroke: s.stroke };
        if (s.dash) attrs["stroke-dasharray"] = s.dash;
        root.appendChild(svg("path", attrs, "ml" + (s.base ? " base" : "")));
        if (opts.dd) {
          var a2 = { d: dd, stroke: s.stroke };
          if (s.dash) a2["stroke-dasharray"] = s.dash;
          root.appendChild(svg("path", a2, "mdd"));
        }
      });
    container.appendChild(root);
  }

  // ── Render ────────────────────────────────────────────────────────
  var lastEnv = null;
  var lastLayout = null;
  var chartEl = null;
  var chartSeries = null;
  var chartOpts = null;
  var chartW = -1;

  function drawIfNeeded(width) {
    if (!chartEl || !chartSeries) return;
    var w = Math.floor(width || chartEl.clientWidth || 0);
    if (w <= 0 || w === chartW) return;
    chartW = w;
    drawMulti(chartEl, chartSeries, chartOpts, w);
    H.notifySize();
  }

  function layoutKey() {
    return H.displayMode() + ":" + (H.isNarrow() ? "narrow" : "wide");
  }

  function errorOf(env) {
    if (!env) return null;
    var v = env.view;
    if (v && typeof v === "object")
      return v.error && !v.runs ? H.prose(v.error) || null : null;
    if (typeof env.code === "string" && typeof env.message === "string")
      // Server prose, so the ids in it are the server's (Q-1712).
      return H.prose(env.message) || env.code.replace(/_/g, " ");
    return null;
  }

  function renderError(body, env, message) {
    body.textContent = "";
    if (document.body) {
      document.body.setAttribute("data-size", "receipt");
      document.body.setAttribute("data-owns-link", "0");
      // An error has nothing to open either.
      document.body.setAttribute("data-no-hero", "1");
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
    var row = el("div", "err-line");
    if (H.wordmark()) row.appendChild(el("span", "brand", "Keel"));
    var v = env && env.view;
    if (v && v.name) {
      var nm = el("span", "rr-name", v.name);
      nm.title = v.name;
      row.appendChild(nm);
    }
    row.appendChild(
      el(
        "span",
        "msg",
        "Comparison unavailable — " + H.withoutFailureLead(message),
      ),
    );
    body.appendChild(row);
    // An error card has nothing to open, so it draws no action band
    // either — the adapter drew one before this card rendered.
    if (typeof H.refreshLinkRow === "function") H.refreshLinkRow();
  }

  function render(env) {
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    chartEl = null;
    chartSeries = null;
    chartW = -1;
    lastLayout = layoutKey();

    var message = errorOf(env);
    if (message) {
      renderError(body, env, message);
      return;
    }
    var v = env && env.view;
    if (!v || !Array.isArray(v.runs) || !v.runs.length) {
      renderError(body, env, "no runs were returned.");
      return;
    }
    if (document.body) {
      document.body.removeAttribute("data-size");
      // The adapter draws the one action row; `LINK_LABELS.compare`
      // names the destination, and there is none when the runs span
      // strategies (`hero_url` absent — Q-1686's rule). Declaring that
      // absence is what stops the row offering to fetch a link that
      // cannot exist; the per-run tearsheets are in the fullscreen
      // table instead.
      document.body.setAttribute("data-owns-link", "0");
      document.body.setAttribute(
        "data-no-hero",
        env && (env.hero_url || env.share_url) ? "0" : "1",
      );
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;

    var full = H.displayMode() === "fullscreen";
    var narrow = H.isNarrow();

    // What tells the runs apart (Q-1780): a shared stem is said once, in
    // the header, and each run is labelled by the rest.
    var dl = distinctLabels(
      v.runs.map(function (r) {
        return String(r.label || "");
      }),
    );
    v.runs.forEach(function (r, i) {
      r._shown = dl.labels[i];
    });

    // Header: Keel · name · N runs · the window, or that they differ.
    var hd = el("div", "cmp-head");
    if (H.wordmark()) hd.appendChild(el("span", "brand", "Keel"));
    var shownName = dl.stem || v.name || "Untitled";
    var name = el("span", "rr-name", shownName);
    name.title = dl.stem
      ? v.runs
          .map(function (r) {
            return r.label;
          })
          .join(" · ")
      : shownName;
    hd.appendChild(name);
    hd.appendChild(
      el(
        "span",
        "chip",
        v.runs.length + (v.runs.length === 1 ? " run" : " runs"),
      ),
    );
    if (v.window) {
      var win = windowText(v.window);
      if (win) hd.appendChild(el("span", "chip muted", win));
      // A MATERIAL difference is a finding (Q-1802); a one-day tail is
      // a note below and earns no chip.
      if (v.overlap && v.overlap.severity === "warning")
        hd.appendChild(el("span", "chip warn", "windows differ"));
    } else {
      // Not a missing value — a finding. The runs do not cover the same
      // period, which is the first thing that makes a comparison unsafe.
      hd.appendChild(el("span", "chip warn", "windows differ"));
    }
    body.appendChild(hd);

    if (full) body.appendChild(table(v, true));
    else if (narrow) body.appendChild(blocks(v));
    else if (v.runs.length <= INLINE_RUNS) body.appendChild(table(v, false));
    else body.appendChild(lines(v));

    // What the numbers belong to — only when nothing contradicts it.
    // A near-identical window (a note, Q-1802) drops "same window": the
    // note below says what differs instead.
    if (!v.warnings || !v.warnings.length) {
      var rc = H.receipt(
        (v.overlap ? [] : ["same window"]).concat([
          "same costs",
          "net of fees, slippage and carry",
        ]),
      );
      if (rc) body.appendChild(rc);
    }

    // The chart: one line per run, at most four inline.
    var withCurve = [];
    var without = [];
    v.runs.forEach(function (r, i) {
      if (readCurve(r.curve)) withCurve.push(i);
      else without.push(i);
    });
    var drawn = full ? withCurve : withCurve.slice(0, INLINE_LINES);
    if (drawn.length) {
      chartSeries = drawn.map(function (i) {
        var s = seriesFor(i);
        return {
          pts: readCurve(v.runs[i].curve),
          label: labelOf(v.runs[i]),
          stroke: s.stroke,
          dash: s.dash,
          base: i === v.baseline,
        };
      });
      // Shaded only for a MATERIAL difference: a one-day tail on a
      // two-year axis is a sub-pixel sliver, and the note says it.
      var ov = overlapOf(v);
      chartOpts = {
        tall: full,
        dd: full,
        overlap: ov && ov.severity === "warning" ? ov : null,
      };
      chartEl = el("div", "chart");
      var legend = el("div", "legend");
      chartSeries.forEach(function (s) {
        var item = el("span");
        var sw = el(
          "span",
          "sw" + (s.dash ? " dash" : "") + (s.base ? " heavy" : ""),
        );
        sw.style.borderTopColor = s.stroke;
        item.appendChild(sw);
        item.appendChild(document.createTextNode(s.label));
        legend.appendChild(item);
      });
      if (withCurve.length > drawn.length) {
        legend.appendChild(
          el(
            "span",
            "rest",
            "+" + (withCurve.length - drawn.length) + " in fullscreen",
          ),
        );
      }
      body.appendChild(legend);
      body.appendChild(chartEl);
      drawIfNeeded(chartEl.clientWidth);
      if (chartW < 0) {
        setTimeout(function () {
          drawIfNeeded(chartEl && (chartEl.clientWidth || 600));
        }, 0);
      }
    }
    without.forEach(function (i) {
      body.appendChild(
        el("p", "note", "no curve for " + labelOf(v.runs[i]).split(" · ")[0]),
      );
    });

    // The comparability warnings, then the notes (Q-1802), capped at
    // two inline.
    var warns = (Array.isArray(v.warnings) ? v.warnings : []).concat(
      Array.isArray(v.notes) ? v.notes : [],
    );
    if (warns.length) {
      body.appendChild(
        H.list(warns, {
          as: "notes",
          cap: full ? warns.length : 2,
          remainder: "more",
        }),
      );
    }
    // Last, just above the action row (S-6): every run compared here is
    // a backtest, so the line always applies.
    body.appendChild(H.historicalNote());
    if (typeof H.refreshLinkRow === "function") H.refreshLinkRow();
  }

  H.ready(function (env) {
    lastEnv = env || {};
    render(lastEnv);
  });

  H.onResize(function (width) {
    if (!lastEnv) return;
    if (layoutKey() !== lastLayout) render(lastEnv);
    else drawIfNeeded(chartEl ? chartEl.clientWidth || width : width);
  });

  // A younger comparison of the same strategy appeared: this one stops
  // competing for the reader's attention (BUILD §2.9). A comparison has
  // no receipt size of its own, so it keeps its header and its table
  // and says so in a chip.
  if (typeof H.onSuperseded === "function") {
    H.onSuperseded(function (key) {
      if (!lastEnv) return;
      render(lastEnv);
      var hd = document.querySelector(".cmp-head");
      if (hd) {
        hd.appendChild(
          el(
            "span",
            "chip muted",
            key && key.version != null
              ? "superseded by v" + key.version
              : "superseded",
          ),
        );
      }
      H.notifySize();
    });
  }
})();
