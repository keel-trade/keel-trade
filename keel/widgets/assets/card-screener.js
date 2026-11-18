/* Screen card: the SAVED VIEW, drawn small for MCP hosts (spec 06 §D3.3,
   §D3.5; INV-53; Q-3635). S1's small card (live-design S1 js/util.js
   `spark` + `cardSVG`, js/screen.js `sparkSVG`/`screenCard`) is the base;
   the layout follows the envelope's `graphs.default_channels`, the view
   keel-api rendered, so the card shows the screen the author set up.

   Inline, by the default view's kind:
   - Table: a ranked table over the view's declared columns, labels as
     written, the unit once in the header, rows = the passing set (or the
     top N) capped at 25, an inline data bar on the sort column (neutral,
     diverging about zero when a shown value is negative), then "+N more in
     Keel" counted against the passing set, never the universe, then the
     ranked output's range over every coin with a value (`range`) when the
     rows show fewer coins than that.
   - Lines: a legend of the shown lines (swatch, coin, value under one
     label line that carries the unit once), then the picture: a y axis
     with ticks, a time axis, the shown lines held to the field's robust
     band (1st–99th percentile) with an edge marker where a line leaves it,
     end labels in each line's colour. Tapping a legend entry isolates its
     line and tapping a line isolates its entry; tap again to clear.
     Tapping the picture's empty space opens fullscreen.
   - Scatter: two `latest` columns as x / y, the legend above.

   Fullscreen: tabs over the published kinds (`graphs.kinds`; River is
   app-only), the Lines picture at full height with a scrub slider, the
   Show: Top N choice and a ranked list; Scatter over two `latest` panel
   columns; Table over every `latest` row with sortable headers.

   An envelope without a result (a receipt, a run in flight, a saved view)
   draws one receipt line, never an empty body.

   Numbers: one formatter (`fmtValue`) and one sign rule (`outputSigned`,
   keel-app's `isSigned`) for every column, legend, axis and line: a column
   prints "+" above zero when its output can go below zero — its declared
   bounds, else the extent the envelope carries over every coin, else the
   unit's nature — never by the rows shown, which for the ranked column of
   a top N are one-signed by construction (Q-3640).

   Colours: S1's 24 `CAT` colours, `--sc-0…23`, assigned by rank through
   RANK_ORDER (so neighbouring ranks are never near-identical: slots 0 and
   9, 3 and 6 are) and frozen per coin for the run.

   Every word comes from the envelope: `copy.*` (served by keel-api) and
   `copy.card.*` (the card's templates). A template the envelope does not
   serve renders nothing: this file carries no English. */
(function () {
  "use strict";
  var H = window.KeelHost;
  var el = H.el;
  var NS = "http://www.w3.org/2000/svg";
  var PALETTE = 24; // --sc-0 … --sc-23 in card-screener.css (S1 CAT)
  // rank → palette slot. Any two ranks within four of each other sit at
  // ΔE ≥ 40 in both themes (tests/test_card_screener_palette.py pins it).
  var RANK_ORDER = [
    0, 14, 19, 8, 7, 5, 11, 10, 22, 2, 15, 20, 17, 4, 18, 1, 21, 9, 6, 13, 12,
    16, 23, 3,
  ];
  var MINUS = "−";
  var ROWS_INLINE = 25;
  var last = null;
  // Per-run UI state: isolated coin, fullscreen tab, Show size, scrub
  // bucket, scatter axes, table sort. Reset when the run changes.
  var ui = { key: null };

  // ---------- served words ----------
  function cardCopy(env) {
    var copy = (env && env.copy) || {};
    return copy.card && typeof copy.card === "object" ? copy.card : {};
  }
  /** A served template filled with values, or null when not served. */
  function tpl(env, key, vars) {
    var t = cardCopy(env)[key];
    if (typeof t !== "string" || !t) return null;
    return t.replace(/\{(\w+)\}/g, function (m, name) {
      return vars && vars[name] != null ? String(vars[name]) : m;
    });
  }
  /** The served word after a number in this unit ("a year"), or "". */
  function unitWord(env, unit) {
    var words = cardCopy(env).units || {};
    return typeof words[unit] === "string" ? words[unit] : "";
  }
  /** Whether the label already states the unit ("Price change, a year"). */
  function labelStatesUnit(label, word) {
    if (!word) return true;
    var l = String(label || "").toLowerCase();
    var noun = word.toLowerCase().split(" ").pop();
    return l.indexOf(word.toLowerCase()) >= 0 || l.indexOf(noun) >= 0;
  }
  /** The column header: the label, then the unit once when it is not said. */
  function headerLabel(env, name) {
    var lab = labelOf(env, name);
    var word = unitWord(env, lab.unit);
    return labelStatesUnit(lab.label, word)
      ? lab.label
      : lab.label + ", " + word;
  }

  // ---------- numbers (S1 `sfmt`/`pct`/`ssPrice` on Keel's storage units) ----------
  /** The one sign rule every branch prints (keel-app's `sign` + `fixed`):
      U+2212 below zero, "+" above zero when the column is signed, nothing
      on a value that rounds to zero at `d` decimals ("0.0%", never
      "−0.0%" or "+0.0%"). */
  function signOf(v, signed, d) {
    var r = d != null && Number(Math.abs(v).toFixed(d)) === 0 ? 0 : v;
    return r < 0 ? MINUS : signed && r > 0 ? "+" : "";
  }
  function fixed(v, d, signed) {
    return signOf(v, signed, d) + Math.abs(v).toFixed(d);
  }
  /** S1 `ssPrice`: ≥ 1000 grouped integers, ≥ 1 two decimals, else three
      significant figures. */
  function price(v, signed) {
    var a = Math.abs(v);
    if (a >= 1000)
      return signOf(v, signed) + Math.round(a).toLocaleString("en-US");
    if (a >= 1) return fixed(v, 2, signed);
    return a === 0 ? "0" : signOf(v, signed) + a.toPrecision(3);
  }
  function sig4(v, signed) {
    return signOf(v, signed) + String(Number(Math.abs(v).toPrecision(4)));
  }
  function isRate(unit) {
    return (
      unit === "percent" ||
      unit === "fraction" ||
      unit === "rate_per_hour" ||
      unit === "rate_per_year"
    );
  }
  /** Keel's units: percent is stored ×100, fraction and rates as decimals.
      S1's display: a sign on signed columns, one decimal for %, U+2212.
      `bare` leaves the unit word off (a header or label line carries it). */
  function fmtValue(env, v, unit, signed, bare) {
    if (v == null || !isFinite(v)) return "—";
    var suffix = bare ? "" : unitWord(env, unit);
    var out;
    if (isRate(unit)) {
      out = fixed(unit === "percent" ? v : v * 100, 1, signed) + "%";
    } else if (unit === "multiple" || unit === "ratio" || unit === "x") {
      out = fixed(v, 2, false) + "×";
    } else if (unit === "price" || unit === "notional") {
      out = price(v, signed);
    } else if (unit === "score") {
      // keel-app's formatScreenValue and the text mirror: two decimals.
      out = fixed(v, 2, signed);
    } else {
      out = sig4(v, signed);
    }
    return suffix ? out + " " + suffix : out;
  }
  /** An axis tick: as few decimals as the step allows, no sign on zero. */
  function fmtTick(env, v, unit, step) {
    if (isRate(unit)) {
      var p = unit === "percent" ? v : v * 100;
      var s = unit === "percent" ? step : step * 100;
      var d = s >= 1 ? 0 : s >= 0.1 ? 1 : 2;
      return fixed(p, d, false) + "%";
    }
    if (unit === "price" || unit === "notional") return price(v, false);
    var k = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    return fixed(v, k, false);
  }

  // ---------- the envelope's facts ----------
  function labelOf(env, name) {
    var outputs = ((env.manifest || {}).outputs || []).map(function (o) {
      return o.name;
    });
    var item = (env.labels || [])[outputs.indexOf(name)] || {};
    return { label: item.label || name || "", unit: item.unit || "" };
  }
  function latestTable(env) {
    var t =
      env.latest && env.latest.rows && env.latest.rows.length
        ? env.latest
        : env.latest_top;
    return t && t.columns ? t : { columns: [], rows: [] };
  }
  function anyBelowZero(values) {
    return values.some(function (v) {
      return typeof v === "number" && v < 0;
    });
  }
  function column(table, name) {
    var ci = table.columns.indexOf(name);
    return ci < 0
      ? null
      : table.rows.map(function (r) {
          return r[ci];
        });
  }
  /** The output's declared bounds (`manifest.outputs[].bounds`, the
      descriptor's domain: an interval or a set), as [min, max], or null. */
  function declaredBounds(env, name) {
    var outs = (env.manifest || {}).outputs || [];
    for (var i = 0; i < outs.length; i++) {
      if (outs[i].name !== name) continue;
      var b = outs[i].bounds || {};
      var vals = (b.interval || b.set || []).filter(function (v) {
        return typeof v === "number" && isFinite(v);
      });
      if (!vals.length) return null;
      return [Math.min.apply(null, vals), Math.max.apply(null, vals)];
    }
    return null;
  }
  /** Whether a column prints "+" above zero: keel-app's `isSigned`
      (screen-format.ts). The output's declared bounds when it has them;
      else whether its values go below zero over an extent the envelope
      settles — `range` (every coin with a value, contract field 1), then
      the whole `latest` table; else a negative anywhere the card can see
      (the hero's history, the shipped rows); else the unit's nature: a
      rate or a score can be negative, a level, a rank or a flag cannot.
      Never the rows shown alone (Q-3640). */
  function outputSigned(env, name) {
    var bounds = declaredBounds(env, name);
    if (bounds) return bounds[0] < 0;
    var r = env.range;
    if (
      r &&
      typeof r === "object" &&
      r.output === name &&
      r.low &&
      typeof r.low.value === "number"
    )
      return r.low.value < 0 || r.below_zero > 0;
    var whole = env.latest;
    if (whole && whole.columns && whole.rows && whole.rows.length) {
      var all = column(whole, name);
      if (all) return anyBelowZero(all);
    }
    var hero = env.hero || {};
    if (
      hero.output === name &&
      (hero.values || []).some(function (s) {
        return anyBelowZero(s || []);
      })
    )
      return true;
    var top = column(latestTable(env), name);
    if (top && anyBelowZero(top)) return true;
    var unit = labelOf(env, name).unit;
    return isRate(unit) || unit === "score";
  }
  /** The view's filter, read off the envelope (contract items 2 and 7):
      `passing` is null when the view has no filter; else it carries the
      count, the total and the tie at the cutoff. A count equal to the
      total is no filter either. */
  function filterInfo(env) {
    var p = env.passing;
    var head = env.headline || {};
    var uni = (env.manifest || {}).universe || {};
    var total = uni.total != null ? uni.total : head.of;
    var none = { filtered: false, count: total, total: total, tie: null };
    if (!p || typeof p !== "object") return none;
    var counts = p.counts || {};
    var count =
      p.count != null
        ? p.count
        : counts.passing != null
          ? counts.passing
          : head.passing;
    var of = p.total != null ? p.total : head.of != null ? head.of : total;
    if (count == null || (of != null && count >= of)) return none;
    return {
      filtered: true,
      count: count,
      total: of,
      tie: p.tie && typeof p.tie === "object" ? p.tie : null,
    };
  }
  /** Hero lines resolved under spec 03's `relative` (rebase / difference). */
  function heroSeries(hero) {
    return hero.assets.map(function (asset, i) {
      var anchor = (hero.anchor_values || {})[asset];
      var raw = hero.values[i] || [];
      var v = raw.map(function (value) {
        var y = value;
        if (y == null || !isFinite(y)) return null;
        if (hero.relative === "rebase_100")
          y = anchor && anchor[1] > 0 ? (y / anchor[1]) * 100 : null;
        else if (hero.relative === "difference")
          y = anchor ? y - anchor[1] : null;
        if (y != null && hero.y_scale === "log" && y <= 0) return null;
        return y;
      });
      var end = null;
      for (var j = raw.length - 1; j >= 0; j--)
        if (raw[j] != null && isFinite(raw[j])) {
          end = raw[j];
          break;
        }
      return { asset: asset, v: v, end: end };
    });
  }
  function slotOf(rank) {
    return RANK_ORDER[rank % PALETTE];
  }
  /** S1 `ssColours`: rank by the colour metric's last value (the ranked
      output, in the run's order); the slot follows the rank through
      RANK_ORDER, frozen per coin for the run. */
  function rankColours(env, series) {
    var desc = ((env.manifest || {}).order || "desc") !== "asc";
    var ranked = series.slice().sort(function (a, b) {
      if (a.end == null && b.end == null) return 0;
      if (a.end == null) return 1;
      if (b.end == null) return -1;
      return desc ? b.end - a.end : a.end - b.end;
    });
    var slot = {};
    ranked.forEach(function (s, r) {
      slot[s.asset] = slotOf(r);
    });
    return { order: ranked, slot: slot };
  }
  function scVar(i) {
    return "var(--sc-" + i + ")";
  }
  /** The whole passing set at the cursor (`hero.passing`, L2), or null. */
  function passingSet(env) {
    var p = (env.hero || {}).passing;
    if (!Array.isArray(p)) return null;
    // The stored list is bounded (the first 50 by name). When it is full and
    // more coins pass than it holds, it is not the passing set: a ranked top
    // would be filtered to nothing and the wrong coins coloured.
    var count = filterInfo(env).count;
    if (count != null && p.length >= 50 && count > p.length) return null;
    return p.map(String);
  }
  /** Colour slots for the coins the screen passes (S1: the filter decides
      what is coloured, the colour metric's rank decides which colour). With
      no served passing set, the hero's rank slots stand. */
  function passSlots(env, model) {
    var pass = passingSet(env);
    if (!pass) return model.slot;
    var inPass = {};
    pass.forEach(function (a) {
      inPass[a] = true;
    });
    var ranked = model.order
      .map(function (s) {
        return s.asset;
      })
      .filter(function (a) {
        return inPass[a];
      });
    var t = latestTable(env);
    t.rows.forEach(function (r) {
      var a = String(r[0]);
      if (inPass[a] && ranked.indexOf(a) < 0) ranked.push(a);
    });
    pass.forEach(function (a) {
      if (ranked.indexOf(a) < 0) ranked.push(a);
    });
    var slot = {};
    ranked.forEach(function (a, r) {
      slot[a] = slotOf(r);
    });
    return slot;
  }
  /** The author's default graph among the kinds the card draws. */
  function defaultKind(env) {
    var g = env.graphs || {};
    var d = (g.default_channels || {}).kind || g.default;
    return d === "Scatter" || d === "Table" ? d : "Lines";
  }
  /** What the default view declares: its columns (in the envelope's
      table), its sort column and direction. Falls back to the ranked
      output over every shipped column. */
  function viewPlan(env) {
    var t = latestTable(env);
    var dc = (env.graphs || {}).default_channels || {};
    var ranked = (env.manifest || {}).ranked_by;
    var cols = (dc.columns || []).filter(function (c) {
      return t.columns.indexOf(c) > 0;
    });
    if (!cols.length) cols = t.columns.slice(1);
    var sort =
      dc.sort && cols.indexOf(dc.sort) >= 0
        ? dc.sort
        : cols.indexOf(ranked) >= 0
          ? ranked
          : cols[0];
    var dir = dc.sort_dir
      ? dc.sort_dir === "asc"
        ? 1
        : -1
      : ((env.manifest || {}).order || "desc") === "asc"
        ? 1
        : -1;
    return { table: t, columns: cols, sort: sort, dir: dir };
  }
  function sortRows(rows, ki, dir) {
    return rows.slice().sort(function (a, b) {
      var x = a[ki];
      var y = b[ki];
      if (typeof x === "string" || typeof y === "string")
        return dir * String(x).localeCompare(String(y));
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return dir * (x - y);
    });
  }
  /** The rows the view shows: the passing set (or the first `count` by
      the sort) when it filters, every row otherwise; sorted by the view's
      sort. `size` is how many coins are in the set, for "+N more". */
  function setRows(env, plan) {
    var fi = filterInfo(env);
    var ki = plan.table.columns.indexOf(plan.sort);
    var rows =
      ki >= 0
        ? sortRows(plan.table.rows, ki, plan.dir)
        : plan.table.rows.slice();
    if (fi.filtered) {
      var pass = passingSet(env);
      var inPass = {};
      (pass || []).forEach(function (a) {
        inPass[a] = true;
      });
      var sel = pass
        ? rows.filter(function (r) {
            return inPass[String(r[0])];
          })
        : [];
      if (sel.length) rows = sel;
      else if (fi.count != null) rows = rows.slice(0, fi.count);
    }
    var size = fi.filtered
      ? fi.count != null
        ? fi.count
        : rows.length
      : fi.total != null
        ? Math.max(fi.total, rows.length)
        : rows.length;
    return { rows: rows, size: size, filter: fi, ki: ki };
  }

  // ---------- drawing ----------
  function svgEl(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    return n;
  }
  function pathD(pts) {
    var d = "";
    var pen = false;
    pts.forEach(function (p) {
      if (!p) {
        pen = false;
        return;
      }
      d += (pen ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1);
      pen = true;
    });
    return d;
  }
  function lastIdx(v) {
    for (var j = v.length - 1; j >= 0; j--) if (v[j] != null) return j;
    return -1;
  }
  function msOf(env, j) {
    var hero = env.hero || {};
    return hero.t0_ms + j * hero.dt_ms;
  }
  function dayOf(env, j) {
    var ms = msOf(env, j);
    return isFinite(ms) ? new Date(ms).toISOString().slice(0, 10) : "";
  }
  /** A time-axis tick: day and month; the hour too when the window is
      short, the year when it is long. UTC, as the as-of line is. */
  function timeTick(env, j) {
    var ms = msOf(env, j);
    if (!isFinite(ms)) return "";
    var hero = env.hero || {};
    var spanDays = (((hero.n || 1) - 1) * (hero.dt_ms || 0)) / 86400000;
    var d = new Date(ms);
    var opts = { timeZone: "UTC", day: "numeric", month: "short" };
    if (spanDays > 400) opts.year = "2-digit";
    var text = d.toLocaleDateString("en-GB", opts);
    if (spanDays <= 3)
      text +=
        " " +
        d.toLocaleTimeString("en-GB", {
          timeZone: "UTC",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        });
    return text;
  }
  function percentile(sorted, q) {
    return sorted[
      Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * q))
    ];
  }
  /** Tick values at a round step, three to five of them inside [lo, hi]. */
  function niceTicks(lo, hi) {
    var span = hi - lo;
    if (!(span > 0)) return { ticks: [lo], step: 1 };
    var raw = span / 3;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var step = [1, 2, 2.5, 5, 10]
      .map(function (m) {
        return m * mag;
      })
      .reduce(function (best, s) {
        return Math.abs(s - raw) < Math.abs(best - raw) ? s : best;
      });
    var ticks = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step)
      ticks.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    return { ticks: ticks, step: step };
  }
  /** S1 `spark` + `drawLines`' labels, with axes (Q-3635): the field
      under, the shown lines on top, both held to the pooled 1st–99th
      percentile band so one spike cannot flatten the rest, an edge marker
      in the line's colour where a line leaves the band, zero dashed when
      the plot crosses it, y ticks on the left, time ticks under, end
      labels nudged 13 px apart, the isolated line kept and every other one
      dimmed (.22). */
  function drawLines(host, env, model, opts) {
    var W = opts.width;
    var Hh = opts.height;
    var unit = model.rebased ? "" : model.unit;
    var endText = function (s) {
      return (
        s.asset +
        " " +
        fmtValue(env, s.v[lastIdx(s.v)], unit, model.signed, true)
      );
    };
    var labelled = model.shown.filter(function (s) {
      return opts.labels.indexOf(s.asset) >= 0;
    });
    var longest = 0;
    labelled.forEach(function (s) {
      longest = Math.max(longest, endText(s).length);
    });
    // The band: the shown lines' pooled values, 1st–99th percentile, let
    // out to the extremes when those sit near the band (a line's ordinary
    // wander) and held when they do not (a spike). The grey field is
    // background: it is drawn inside the band and may leave it unmarked.
    var pool = [];
    model.shown.forEach(function (s) {
      s.v.forEach(function (v) {
        if (v != null) pool.push(v);
      });
    });
    pool.sort(function (a, b) {
      return a - b;
    });
    var lo;
    var hi;
    if (!pool.length) {
      lo = 0;
      hi = 1;
    } else if (pool.length < 40) {
      lo = pool[0];
      hi = pool[pool.length - 1];
    } else {
      lo = percentile(pool, 0.01);
      hi = percentile(pool, 0.99);
      var slack = (hi - lo) * 0.3;
      if (lo - pool[0] <= slack) lo = pool[0];
      if (pool[pool.length - 1] - hi <= slack) hi = pool[pool.length - 1];
    }
    if (hi <= lo) {
      hi = lo + (Math.abs(lo) || 1);
      lo = lo - (Math.abs(lo) || 1);
    }
    var log = model.log && lo > 0;
    var f = log
      ? Math.log
      : function (v) {
          return v;
        };
    var a0 = f(lo);
    var a1 = f(hi);
    var pad = (a1 - a0) * 0.06 || 1;
    a0 -= pad;
    a1 += pad;
    var tick = niceTicks(log ? Math.exp(a0) : a0, log ? Math.exp(a1) : a1);
    var tickText = function (v) {
      return fmtTick(env, v, unit, tick.step);
    };
    var gutter = 0;
    tick.ticks.forEach(function (v) {
      gutter = Math.max(gutter, tickText(v).length);
    });
    var P = {
      l: 8 + 6.6 * gutter,
      r: labelled.length ? Math.min(150, 10 + 6.8 * longest) : 6,
      t: 8,
      b: 20,
    };
    var pw = W - P.l - P.r;
    var ph = Hh - P.t - P.b;
    var n = model.n;
    var X = function (j) {
      return P.l + (j / Math.max(1, n - 1)) * pw;
    };
    var Y = function (v) {
      return P.t + ((a1 - f(v)) / (a1 - a0)) * ph;
    };
    var pts = function (v) {
      return v.map(function (y, j) {
        return y == null ? null : [X(j), Y(y)];
      });
    };
    var svg = svgEl("svg", {
      class: "sc-svg",
      width: W,
      height: Hh,
      viewBox: "0 0 " + W + " " + Hh,
      role: "img",
      "aria-label": opts.aria || null,
    });
    var clip = "sc-clip-" + opts.id;
    var defs = svgEl("defs", {});
    var cr = svgEl("clipPath", { id: clip });
    cr.appendChild(svgEl("rect", { x: P.l, y: P.t, width: pw, height: ph }));
    defs.appendChild(cr);
    svg.appendChild(defs);
    // the y axis: a faint rule and its value at each tick
    tick.ticks.forEach(function (v) {
      if (!(v >= lo - pad && v <= hi + pad) && !log) return;
      var y = Y(v);
      if (y < P.t - 1 || y > P.t + ph + 1) return;
      svg.appendChild(
        svgEl("line", {
          class: "sc-grid",
          x1: P.l,
          x2: P.l + pw,
          y1: y,
          y2: y,
        }),
      );
      var t = svgEl("text", {
        class: "sc-tick sc-ytick",
        x: P.l - 5,
        y: y,
        "text-anchor": "end",
      });
      t.textContent = tickText(v);
      svg.appendChild(t);
    });
    var g = svgEl("g", { "clip-path": "url(#" + clip + ")" });
    model.field.forEach(function (s) {
      g.appendChild(svgEl("path", { class: "sc-field", d: pathD(pts(s.v)) }));
    });
    if (model.zero != null && lo < model.zero && hi > model.zero)
      g.appendChild(
        svgEl("line", {
          class: "sc-zero",
          x1: P.l,
          x2: P.l + pw,
          y1: Y(model.zero),
          y2: Y(model.zero),
        }),
      );
    var iso = model.iso;
    var clipped = [];
    model.shown
      .slice()
      .reverse()
      .forEach(function (s) {
        var d = pathD(pts(s.v));
        g.appendChild(
          svgEl("path", {
            class: "ml sc-line" + (iso && iso !== s.asset ? " dim" : ""),
            d: d,
            stroke: scVar(model.slot[s.asset]),
            "stroke-width": iso === s.asset ? 2.6 : W >= 480 ? 1.7 : 1.5,
            "data-asset": s.asset,
          }),
        );
        g.appendChild(
          svgEl("path", { class: "sc-hit", d: d, "data-asset": s.asset }),
        );
        // where the line leaves the band: its farthest point, above or below
        var over = null;
        var under = null;
        s.v.forEach(function (v, j) {
          if (v == null) return;
          if (v > hi && (!over || v > over.v)) over = { v: v, j: j };
          if (v < lo && (!under || v < under.v)) under = { v: v, j: j };
        });
        [over, under].forEach(function (c, side) {
          if (c) clipped.push({ s: s, j: c.j, v: c.v, top: side === 0 });
        });
      });
    if (opts.cursor != null) {
      var cx = X(opts.cursor);
      g.appendChild(
        svgEl("line", {
          class: "sc-cursor",
          x1: cx,
          x2: cx,
          y1: P.t,
          y2: P.t + ph,
        }),
      );
    }
    svg.appendChild(g);
    // edge markers: a small triangle at the edge, in the line's colour,
    // pointing the way the line went; its title is the coin and the value
    clipped.forEach(function (c) {
      var x = X(c.j);
      var y = c.top ? P.t + 1 : P.t + ph - 1;
      var dy = c.top ? 6 : -6;
      var m = svgEl("path", {
        class: "sc-clip" + (iso && iso !== c.s.asset ? " dim" : ""),
        d: "M" + x.toFixed(1) + " " + y.toFixed(1) + "l-4.5 " + dy + "h9z",
        fill: scVar(model.slot[c.s.asset]),
        "data-asset": c.s.asset,
      });
      var title = svgEl("title", {});
      title.textContent =
        c.s.asset + " " + fmtValue(env, c.v, unit, model.signed, true);
      m.appendChild(title);
      svg.appendChild(m);
    });
    var pl = [];
    labelled.forEach(function (s) {
      var j = lastIdx(s.v);
      if (j >= 0) {
        var v = s.v[j];
        var off = v > hi ? -1 : v < lo ? 1 : 0;
        pl.push({
          y: Math.max(P.t + 10, Math.min(P.t + ph, Y(v))),
          x: X(j),
          s: s,
          off: off,
        });
      }
    });
    pl.sort(function (p, q) {
      return p.y - q.y;
    });
    // At most one label per 13 px of plot; the rest keep their line colour
    // and are read from the legend. Spread downwards, then pull back up
    // from the bottom edge, so a label never leaves its line's neighbourhood
    // for another line's (a uniform shift named the wrong lines).
    var cap = Math.max(1, Math.floor(ph / 13));
    if (pl.length > cap) pl = pl.slice(0, cap);
    for (var k = 1; k < pl.length; k++)
      if (pl[k].y - pl[k - 1].y < 13) pl[k].y = pl[k - 1].y + 13;
    if (pl.length) {
      var lastI = pl.length - 1;
      pl[lastI].y = Math.min(pl[lastI].y, P.t + ph);
      for (var m2 = lastI - 1; m2 >= 0; m2--)
        pl[m2].y = Math.min(pl[m2].y, pl[m2 + 1].y - 13);
      pl.forEach(function (p) {
        p.y = Math.max(p.y, P.t + 10);
      });
    }
    pl.forEach(function (p) {
      var t = svgEl("text", {
        class: "sc-end" + (iso && iso !== p.s.asset ? " dim" : ""),
        x: p.x + 6,
        y: p.y,
        fill: scVar(model.slot[p.s.asset]),
        "data-asset": p.s.asset,
      });
      // a line that ends off the band: its label sits at the edge and says so
      t.textContent = (p.off < 0 ? "▲ " : p.off > 0 ? "▼ " : "") + endText(p.s);
      svg.appendChild(t);
    });
    // the time axis: start, middle and end of the window
    var xs = n > 2 ? [0, Math.floor((n - 1) / 2), n - 1] : [0, n - 1];
    xs.forEach(function (j, e) {
      var t = svgEl("text", {
        class: "sc-tick sc-xtick",
        x: X(j),
        y: Hh - 5,
        "text-anchor":
          e === 0 ? "start" : e === xs.length - 1 ? "end" : "middle",
      });
      t.textContent = timeTick(env, j);
      svg.appendChild(t);
    });
    host.appendChild(svg);
  }

  // ---------- the card ----------
  function render(env) {
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    var view = env.view || {};
    if (env.code && env.message) {
      body.appendChild(el("p", "state", env.message));
      H.notifySize();
      return;
    }
    var copy = env.copy || {};
    var screen = env.screener || {};
    H.header({ name: view.name, version: view.version });
    var rev =
      screen.view_revision != null
        ? tpl(env, "view_rev", { n: screen.view_revision })
        : null;
    if (view.size === "receipt" || !env.manifest) {
      // No result to draw (a receipt, a run in flight, a saved view): one
      // line, never an empty body.
      var run = env.run || {};
      var words =
        run.error_words ||
        run.status_words ||
        copy.freshness ||
        copy.description;
      var line = H.receipt([words, rev]);
      body.appendChild(line || el("p", "state", view.name || ""));
      H.notifySize();
      return;
    }
    var runKey = (env.run || {}).run_id || view.object || "";
    if (ui.key !== runKey)
      ui = { key: runKey, iso: null, tab: null, top: null, at: null };
    var full = H.displayMode() === "fullscreen";
    // the quiet meta line: cadence · as of · view rev · freshness · refresh
    var asOf =
      copy.as_of && typeof copy.as_of === "object" ? copy.as_of.words : null;
    var meta = [copy.clock, asOf, rev, copy.freshness, screen.refresh_notice]
      .filter(function (p) {
        return typeof p === "string" && p;
      })
      .join(" · ");
    if (meta) body.appendChild(el("p", "sc-meta muted", meta));
    var hero =
      env.hero && env.hero.assets && env.hero.values && env.hero.n
        ? env.hero
        : null;
    var rc = rankColours(env, hero ? heroSeries(hero) : []);
    var model = { slot: rc.slot, order: rc.order, n: hero ? hero.n : 0 };
    var kinds = publishedKinds(env);
    var tab = full ? ui.tab : defaultKind(env);
    filterLine(body, env);
    if (full && kinds.length > 1) tabs(body, kinds);
    if (tab === "Table") table(body, env, model, !full);
    else {
      numbers(body, env, model);
      if (tab === "Scatter") scatter(body, env, model, !full);
      else if (hero) lines(body, env, model, full);
    }
    // the view's own sentence, then the honesty lines
    [
      full && tab !== "Table" ? copy.ranking : null,
      copy.coverage,
      copy.gaps,
      copy.rebased,
    ]
      .filter(Boolean)
      .forEach(function (line) {
        body.appendChild(el("p", "muted sc-cap", line));
      });
    if (!full && kinds.length > 1) {
      var others = kinds.filter(function (k) {
        return k !== tab;
      });
      var mv = tpl(env, "more_views");
      if (mv)
        body.appendChild(
          el("p", "muted sc-cap", mv + ": " + others.join(" · ")),
        );
    } else if (full) {
      var mvf = tpl(env, "more_views");
      if (mvf) body.appendChild(el("p", "muted sc-cap", mvf));
    }
    // The adapter owns the canonical Open link; add only the other action.
    (env.actions || [])
      .filter(function (a) {
        return a.url !== env.hero_url;
      })
      .slice(0, 1)
      .forEach(function (action) {
        // Through the host bridge (ui/open-link, openExternal): a sandbox
        // without allow-popups blocks a plain target=_blank link.
        var a = el("a", "action", action.label);
        a.href = action.url;
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          H.openLink(action.url);
        });
        body.appendChild(a);
      });
    H.notifySize();
  }
  function isolate(asset) {
    ui.iso = ui.iso === asset ? null : asset;
    if (last) render(last);
  }

  /** The filter in words: "{count} of {total} pass" (served), the view's
      own sentence, and the tie at the cutoff when there is one. Nothing
      when the view has no filter. */
  function filterLine(body, env) {
    var fi = filterInfo(env);
    if (!fi.filtered) {
      var desc = (env.copy || {}).description;
      if (desc) body.appendChild(el("p", "muted sc-cap", desc));
      return;
    }
    var pass = tpl(env, "pass_now", { passing: fi.count, of: fi.total });
    var description = (env.copy || {}).description;
    if (pass || description) {
      var p = el("p", "sc-pass");
      if (pass) p.appendChild(el("span", "sc-passn", pass));
      if (description) p.appendChild(el("span", "muted sc-passd", description));
      body.appendChild(p);
    }
    if (fi.tie && fi.tie.count > 1 && typeof fi.tie.value === "number") {
      var plan = viewPlan(env);
      var lab = labelOf(env, plan.sort);
      var tie = tpl(env, "tie", {
        n: fi.tie.count,
        value: fmtValue(
          env,
          fi.tie.value,
          lab.unit,
          outputSigned(env, plan.sort),
        ),
      });
      if (tie) body.appendChild(el("p", "muted sc-cap sc-tie", tie));
    }
  }

  /** The ranked output's extent over every coin with a value (`range`,
      contract field 1; Q-3640): one quiet line under the rows when they
      show fewer coins than that, in the column's unit and sign, with the
      below-zero count when there is one. Nothing without the served
      template. */
  function rangeLine(body, env, shown) {
    var r = env.range;
    if (!r || typeof r !== "object" || !r.low || !r.high) return;
    if (!(typeof r.count === "number" && r.count > shown)) return;
    var name = r.output;
    var unit = labelOf(env, name).unit;
    var signed = outputSigned(env, name);
    var line = tpl(env, "range", {
      count: r.count,
      low: fmtValue(env, r.low.value, unit, signed, true),
      low_asset: r.low.asset,
      high: fmtValue(env, r.high.value, unit, signed, true),
      high_asset: r.high.asset,
      label: headerLabel(env, name),
    });
    if (!line) return;
    var below =
      r.below_zero > 0 ? tpl(env, "range_below", { n: r.below_zero }) : null;
    body.appendChild(
      el("p", "muted sc-cap sc-range", below ? line + " · " + below : line),
    );
  }

  /** The legend of the shown lines (S1's labelled numbers, §D3.3): one
      label line carrying the unit once, then swatch · coin · value for the
      top five of the set. "+N more in Keel" counts the set, not the
      universe; the range line follows. */
  function numbers(body, env, model) {
    var plan = viewPlan(env);
    var t = plan.table;
    var hero = env.hero || {};
    var col = t.columns.indexOf(hero.output) > 0 ? hero.output : plan.sort;
    var ci = t.columns.indexOf(col);
    var lab = labelOf(env, col);
    var set = setRows(env, plan);
    var rows = ci < 0 ? [] : set.rows.slice(0, 5);
    var slots = passSlots(env, model);
    var signed = ci >= 0 && outputSigned(env, col);
    var wrap = el("div", "sc-nums");
    if (rows.length) {
      wrap.appendChild(el("p", "sc-numlab", headerLabel(env, col)));
      var grid = el("div", "sc-legend");
      rows.forEach(function (row) {
        var asset = String(row[0]);
        var b = el("button", "sc-num" + (ui.iso === asset ? " on" : ""));
        b.type = "button";
        b.setAttribute("aria-pressed", ui.iso === asset ? "true" : "false");
        b.setAttribute("data-asset", asset);
        b.setAttribute(
          "aria-label",
          asset + " " + fmtValue(env, row[ci], lab.unit, signed),
        );
        var sw = el("i", "sc-sw");
        if (slots[asset] != null) sw.style.background = scVar(slots[asset]);
        b.appendChild(sw);
        b.appendChild(el("span", "k", asset));
        b.appendChild(
          el("span", "v", fmtValue(env, row[ci], lab.unit, signed, true)),
        );
        b.addEventListener("click", function () {
          isolate(asset);
        });
        grid.appendChild(b);
      });
      wrap.appendChild(grid);
    }
    if (set.size > rows.length) {
      var more = tpl(env, "more_in_keel", { k: set.size - rows.length });
      if (more) {
        var m = el("span", "sc-more muted", more);
        if (rows.length) wrap.lastChild.appendChild(m);
        else wrap.appendChild(m);
      }
    }
    body.appendChild(wrap);
    rangeLine(body, env, rows.length);
  }

  /** The published kinds the card draws (River is app-only), in spec
      03's order; the author's default opens first. */
  function publishedKinds(env) {
    var g = env.graphs || {};
    var kinds = ["Lines", "Scatter", "Table"].filter(function (k) {
      return (g.kinds || []).indexOf(k) >= 0;
    });
    if (!kinds.length) kinds = [defaultKind(env)];
    if (ui.tab == null || kinds.indexOf(ui.tab) < 0)
      ui.tab =
        kinds.indexOf(defaultKind(env)) >= 0 ? defaultKind(env) : kinds[0];
    return kinds;
  }
  function seg(items, current, onPick, cls) {
    var bar = el("div", "sc-seg" + (cls ? " " + cls : ""));
    bar.setAttribute("role", "group");
    items.forEach(function (it) {
      var b = el("button", it.value === current ? "on" : "", it.label);
      b.type = "button";
      b.setAttribute("aria-pressed", it.value === current ? "true" : "false");
      b.addEventListener("click", function () {
        onPick(it.value);
      });
      bar.appendChild(b);
    });
    return bar;
  }
  function tabs(body, kinds) {
    body.appendChild(
      seg(
        kinds.map(function (k) {
          return { value: k, label: k };
        }),
        ui.tab,
        function (k) {
          ui.tab = k;
          render(last);
        },
        "sc-tabs",
      ),
    );
  }

  /** The inline picture, or the fullscreen Lines tab (§D3.3, §D3.5). */
  function lines(body, env, model, full) {
    var width = H.cardWidth() || 600;
    var all = model.order;
    var pass = passingSet(env);
    if (pass && filterInfo(env).filtered) {
      // the filter decides what is coloured: the passing coins lead
      var inPass = {};
      pass.forEach(function (a) {
        inPass[a] = true;
      });
      all = all
        .filter(function (s) {
          return inPass[s.asset];
        })
        .concat(
          all.filter(function (s) {
            return !inPass[s.asset];
          }),
        );
    }
    var cap = full ? all.length : width >= 480 ? 20 : 10;
    var g = env.graphs || {};
    var hero = env.hero || {};
    var shownN = full
      ? Math.min(ui.top != null ? ui.top : all.length, all.length)
      : Math.min(5, all.length);
    var shown = all.slice(0, shownN);
    if (
      ui.iso &&
      shown.every(function (s) {
        return s.asset !== ui.iso;
      })
    )
      // a pinned (isolated) coin is never cut (spec 03 §4.8)
      shown = shown.concat(
        all.filter(function (s) {
          return s.asset === ui.iso;
        }),
      );
    var field = all.slice(0, cap).filter(function (s) {
      return shown.indexOf(s) < 0;
    });
    var lab = labelOf(env, hero.output);
    var signed =
      hero.relative === "difference" || outputSigned(env, hero.output);
    var m = {
      shown: shown,
      field: field,
      slot: model.slot,
      n: model.n,
      zero: hero.relative === "rebase_100" ? 100 : 0,
      iso: ui.iso,
      signed: signed,
      unit: lab.unit,
      rebased: hero.relative === "rebase_100",
      log: hero.y_scale === "log",
    };
    var sizes = [1, 5, 10, 20].filter(function (s) {
      return s < all.length;
    });
    if (full && !g.top_control && sizes.length) {
      var items = sizes.map(function (s) {
        return { value: s, label: String(s) };
      });
      items.push({
        value: all.length,
        label: tpl(env, "show_all", { n: all.length }) || String(all.length),
      });
      var row = el("div", "sc-row");
      var showWord = tpl(env, "show");
      if (showWord) row.appendChild(el("span", "sc-lab", showWord));
      row.appendChild(
        seg(items, shownN, function (v) {
          ui.top = v;
          render(last);
        }),
      );
      body.appendChild(row);
    }
    var chart = el("div", "chart sc-chart" + (full ? " tall" : ""));
    body.appendChild(chart);
    var labels = shown
      .map(function (s) {
        return s.asset;
      })
      .slice(0, full ? (width < 480 ? 6 : 24) : 5);
    if (ui.iso && labels.indexOf(ui.iso) < 0) labels.push(ui.iso);
    var at = full && ui.at != null ? Math.min(ui.at, model.n - 1) : null;
    drawLines(chart, env, m, {
      width: Math.max(240, (chart.clientWidth || width) - 2),
      height: full ? 360 : width < 480 ? 150 : 170,
      labels: labels,
      cursor: at,
      id: full ? "f" : "i",
      aria: headerLabel(env, hero.output),
    });
    var canExpand = !full && H.availableDisplayModes.indexOf("fullscreen") >= 0;
    chart.addEventListener("click", function (event) {
      var a =
        event.target &&
        event.target.getAttribute &&
        event.target.getAttribute("data-asset");
      if (a) {
        event.stopPropagation();
        isolate(a);
      } else if (canExpand) H.requestDisplayMode("fullscreen");
    });
    if (canExpand) {
      chart.tabIndex = 0;
      chart.setAttribute("role", "button");
      var expand = tpl(env, "expand");
      if (expand) chart.setAttribute("aria-label", expand);
      chart.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          H.requestDisplayMode("fullscreen");
        }
      });
    }
    if (!full) return;
    var caption = tpl(env, "caption", {
      ranked_by: lab.label,
      n: model.n,
      source_points: hero.source_points != null ? hero.source_points : model.n,
      k: shown.length,
      A: ((env.manifest || {}).universe || {}).total,
    });
    if (caption) body.appendChild(el("p", "sc-cap muted", caption));
    // the scrub slider over the grid and its readout (S1 `readout`)
    var slider = el("input", "sc-slider");
    slider.type = "range";
    slider.min = "0";
    slider.max = String(Math.max(0, model.n - 1));
    slider.value = String(at == null ? model.n - 1 : at);
    var scrub = tpl(env, "scrub");
    if (scrub) slider.setAttribute("aria-label", scrub);
    var read = el("p", "sc-read");
    var paint = function (j) {
      var vals = shown
        .filter(function (s) {
          return s.v[j] != null && (!ui.iso || s.asset === ui.iso);
        })
        .sort(function (p, q) {
          return q.v[j] - p.v[j];
        });
      read.textContent =
        dayOf(env, j) +
        " · " +
        vals
          .slice(0, 6)
          .map(function (s) {
            return (
              s.asset +
              " " +
              fmtValue(env, s.v[j], m.rebased ? "" : lab.unit, signed, true)
            );
          })
          .join("   ");
    };
    paint(Number(slider.value));
    slider.addEventListener("input", function () {
      paint(Number(slider.value));
    });
    slider.addEventListener("change", function () {
      ui.at = Number(slider.value);
      render(last);
    });
    body.appendChild(slider);
    body.appendChild(read);
    var note = tpl(env, "latest_note");
    if (note) body.appendChild(el("p", "muted", note));
    // the ranked list (S1 `listHTML`): rank, swatch, name, last value
    var list = el("div", "sc-list");
    shown.forEach(function (s, r) {
      var it = el("button", "sc-it" + (ui.iso === s.asset ? " on" : ""));
      it.type = "button";
      it.setAttribute("aria-pressed", ui.iso === s.asset ? "true" : "false");
      it.appendChild(el("span", "rk", String(r + 1)));
      var sw = el("i", "sc-sw");
      sw.style.background = scVar(model.slot[s.asset]);
      it.appendChild(sw);
      it.appendChild(el("span", "nm", s.asset));
      it.appendChild(el("span", "v", fmtValue(env, s.end, lab.unit, signed)));
      it.addEventListener("click", function () {
        isolate(s.asset);
      });
      list.appendChild(it);
    });
    body.appendChild(list);
  }

  /** Two `latest` panel columns as x / y, one dot per asset (§D3.5; S1
      `ssScatterCanvas`: coloured r 5 on top, others grey r 3, median
      quadrant lines, labels when ≤ 12 are coloured). */
  function scatter(body, env, model, inline) {
    var t = latestTable(env);
    var panel = ((env.manifest || {}).outputs || [])
      .filter(function (o) {
        return (
          (o.shape || "panel") === "panel" && t.columns.indexOf(o.name) >= 0
        );
      })
      .map(function (o) {
        return o.name;
      });
    if (panel.length < 2) return;
    // the default view's own x / y when the envelope names them
    var dc = (env.graphs || {}).default_channels || {};
    var dx = panel.indexOf(dc.x) >= 0 ? dc.x : panel[1];
    var dy = panel.indexOf(dc.y) >= 0 ? dc.y : panel[0];
    var xs = !inline && panel.indexOf(ui.sx) >= 0 ? ui.sx : dx;
    var ys = !inline && panel.indexOf(ui.sy) >= 0 ? ui.sy : dy;
    var choices = panel.map(function (n) {
      return { value: n, label: labelOf(env, n).label };
    });
    (inline
      ? []
      : [
          ["Y", ys, "sy"],
          ["X", xs, "sx"],
        ]
    ).forEach(function (axis) {
      var row = el("div", "sc-row");
      var letter = el("span", "sc-lab", axis[0]);
      letter.id = "sc-axis-" + axis[2];
      row.appendChild(letter);
      var picker = seg(choices, axis[1], function (v) {
        ui[axis[2]] = v;
        render(last);
      });
      picker.setAttribute("aria-labelledby", letter.id);
      row.appendChild(picker);
      body.appendChild(row);
    });
    var xi = t.columns.indexOf(xs);
    var yi = t.columns.indexOf(ys);
    var pts = t.rows
      .map(function (r) {
        return { a: String(r[0]), x: r[xi], y: r[yi] };
      })
      .filter(function (p) {
        return (
          typeof p.x === "number" &&
          typeof p.y === "number" &&
          isFinite(p.x) &&
          isFinite(p.y)
        );
      });
    // No coin has both values (e.g. a source gap at the latest bar): draw no
    // picture rather than axes over an empty extent (NaN ticks).
    if (!pts.length) return;
    var lx = labelOf(env, xs);
    var ly = labelOf(env, ys);
    var sxS = outputSigned(env, xs);
    var syS = outputSigned(env, ys);
    var slot = passSlots(env, model);
    var chart = el("div", "chart sc-chart" + (inline ? "" : " tall"));
    body.appendChild(chart);
    var W = Math.max(240, (chart.clientWidth || H.cardWidth() || 600) - 2);
    var Hh = inline ? (W < 480 ? 200 : 240) : W < 480 ? 300 : 360;
    var P = { l: 64, r: 16, t: 14, b: 40 };
    var ext = function (k) {
      var v = pts.map(function (p) {
        return p[k];
      });
      var lo = Math.min.apply(null, v);
      var hi = Math.max.apply(null, v);
      var pd = (hi - lo) * 0.06 || 1;
      return [lo - pd, hi + pd];
    };
    var xr = ext("x");
    var yr = ext("y");
    // a declared quadrant line always sits inside the plot
    if (typeof dc.quadrant_x === "number") {
      xr[0] = Math.min(xr[0], dc.quadrant_x);
      xr[1] = Math.max(xr[1], dc.quadrant_x);
    }
    if (typeof dc.quadrant_y === "number") {
      yr[0] = Math.min(yr[0], dc.quadrant_y);
      yr[1] = Math.max(yr[1], dc.quadrant_y);
    }
    var X = function (v) {
      return P.l + ((v - xr[0]) / (xr[1] - xr[0])) * (W - P.l - P.r);
    };
    var Y = function (v) {
      return P.t + (1 - (v - yr[0]) / (yr[1] - yr[0])) * (Hh - P.t - P.b);
    };
    var med = function (k) {
      var s = pts
        .map(function (p) {
          return p[k];
        })
        .sort(function (a, b) {
          return a - b;
        });
      return s[Math.floor(s.length / 2)];
    };
    var svg = svgEl("svg", {
      class: "sc-svg",
      width: W,
      height: Hh,
      viewBox: "0 0 " + W + " " + Hh,
      role: "img",
      "aria-label": ly.label + " · " + lx.label,
    });
    if (pts.length) {
      // the author's declared quadrant lines (default_channels.quadrant_x/y,
      // a number) win over the medians
      var num = function (v) {
        return typeof v === "number" && isFinite(v) ? v : null;
      };
      var mx = num(dc.quadrant_x);
      var my = num(dc.quadrant_y);
      if (mx === null) mx = med("x");
      if (my === null) my = med("y");
      svg.appendChild(
        svgEl("line", {
          class: "sc-median",
          x1: X(mx),
          x2: X(mx),
          y1: P.t,
          y2: Hh - P.b,
        }),
      );
      svg.appendChild(
        svgEl("line", {
          class: "sc-median",
          x1: P.l,
          x2: W - P.r,
          y1: Y(my),
          y2: Y(my),
        }),
      );
      // the quadrants named in served words only (`copy.card.quadrants`),
      // keyed {top_left, top_right, bottom_left, bottom_right} (L2
      // ced050e701: the author's readings, else the axis words), or a list
      var qw = cardCopy(env).quadrants;
      if (qw && !Array.isArray(qw) && typeof qw === "object")
        qw = [qw.top_left, qw.top_right, qw.bottom_left, qw.bottom_right];
      if (Array.isArray(qw) && qw.length === 4)
        [
          [P.l + 6, P.t + 12, "start"],
          [W - P.r - 6, P.t + 12, "end"],
          [P.l + 6, Hh - P.b - 6, "start"],
          [W - P.r - 6, Hh - P.b - 6, "end"],
        ].forEach(function (q, i) {
          if (typeof qw[i] !== "string" || !qw[i]) return;
          var tq = svgEl("text", {
            class: "sc-quad",
            x: q[0],
            y: q[1],
            "text-anchor": q[2],
          });
          tq.textContent = qw[i];
          svg.appendChild(tq);
        });
    }
    [0, 0.5, 1].forEach(function (q, e) {
      var vx = xr[0] + q * (xr[1] - xr[0]);
      var vy = yr[0] + q * (yr[1] - yr[0]);
      var tx = svgEl("text", {
        class: "sc-tick",
        x: X(vx),
        y: Hh - P.b + 16,
        "text-anchor": e === 0 ? "start" : e === 2 ? "end" : "middle",
      });
      tx.textContent = fmtValue(env, vx, lx.unit, sxS, true);
      svg.appendChild(tx);
      var ty = svgEl("text", {
        class: "sc-tick",
        x: P.l - 6,
        y: Y(vy),
        "text-anchor": "end",
        "dominant-baseline": "middle",
      });
      ty.textContent = fmtValue(env, vy, ly.unit, syS, true);
      svg.appendChild(ty);
    });
    var midY = P.t + (Hh - P.t - P.b) / 2;
    var ax = svgEl("text", {
      class: "sc-axis",
      x: P.l + (W - P.l - P.r) / 2,
      y: Hh - 4,
      "text-anchor": "middle",
    });
    ax.textContent = "X · " + headerLabel(env, xs);
    svg.appendChild(ax);
    var ay = svgEl("text", {
      class: "sc-axis",
      x: 11,
      y: midY,
      "text-anchor": "middle",
      transform: "rotate(-90 11 " + midY + ")",
    });
    ay.textContent = "Y · " + headerLabel(env, ys);
    svg.appendChild(ay);
    var coloured = pts.filter(function (p) {
      return slot[p.a] != null;
    }).length;
    pts
      .slice()
      .sort(function (p, q) {
        return (slot[p.a] != null) - (slot[q.a] != null);
      })
      .forEach(function (p) {
        var on = slot[p.a] != null;
        var c = svgEl("circle", {
          class:
            "sc-dot" +
            (on ? "" : " out") +
            (ui.iso && ui.iso !== p.a ? " dim" : ""),
          cx: X(p.x),
          cy: Y(p.y),
          r: on ? 5 : 3,
          fill: on ? scVar(slot[p.a]) : null,
          "data-asset": p.a,
        });
        var title = svgEl("title", {});
        title.textContent =
          p.a +
          " · X " +
          fmtValue(env, p.x, lx.unit, sxS) +
          " · Y " +
          fmtValue(env, p.y, ly.unit, syS);
        c.appendChild(title);
        svg.appendChild(c);
        if (ui.iso === p.a || (on && coloured <= (inline ? 6 : 12))) {
          var lab = svgEl("text", {
            class: "sc-dotlab",
            x: X(p.x) + 8,
            y: Y(p.y),
            "dominant-baseline": "middle",
          });
          lab.textContent = p.a;
          svg.appendChild(lab);
        }
      });
    chart.appendChild(svg);
    chart.addEventListener("click", function (event) {
      var a =
        event.target &&
        event.target.getAttribute &&
        event.target.getAttribute("data-asset");
      if (a) isolate(a);
    });
    if (inline) return;
    var cap = tpl(env, "scatter_caption", {
      n: pts.length,
      A: ((env.manifest || {}).universe || {}).total,
      ranked_by: labelOf(env, (env.manifest || {}).ranked_by).label,
    });
    if (cap) body.appendChild(el("p", "sc-cap muted", cap));
  }

  /** The ranked table: the view's columns, the set's rows (§D3.5; Q-3635).
      Inline: rank, coin, the sort column with its data bar, more columns
      as the width allows, 25 rows, then "+N more in Keel" against the set,
      then the range line. Fullscreen: every column and row, headers sort
      (↓/↑). The bar is neutral — colour here would separate nothing — and
      diverges about zero when a shown value is negative. */
  function table(body, env, model, inline) {
    var plan = viewPlan(env);
    var set = setRows(env, plan);
    if (!set.rows.length) return;
    var whole = plan.table;
    var width = H.cardWidth() || 600;
    var cols = plan.columns.slice();
    var key = plan.sort;
    var dir = plan.dir;
    if (
      !inline &&
      ui.sort &&
      (ui.sort.key === "" || whole.columns.indexOf(ui.sort.key) >= 0)
    ) {
      key = ui.sort.key;
      dir = ui.sort.dir;
    }
    if (inline) {
      var allow = width < 400 ? 2 : width < 560 ? 3 : cols.length;
      var kept = [plan.sort].concat(
        cols.filter(function (c) {
          return c !== plan.sort;
        }),
      );
      // the view's order, the sort column always among them
      kept = kept.slice(0, allow);
      cols = cols.filter(function (c) {
        return kept.indexOf(c) >= 0;
      });
    }
    var rows = set.rows;
    if (key !== plan.sort || dir !== plan.dir) {
      var ki = key === "" ? 0 : whole.columns.indexOf(key);
      rows = sortRows(rows, ki, dir);
    }
    var shown = inline ? rows.slice(0, ROWS_INLINE) : rows;
    var wrap = el("div", "sc-tw");
    var tbl = el("table", "sc-dt sc-ranked");
    var thead = el("thead");
    var hr = el("tr");
    var headers = [
      { c: null, text: "#", cls: "rk" },
      { c: "", text: "", cls: "t" },
    ].concat(
      cols.map(function (c) {
        return { c: c, text: headerLabel(env, c), cls: "n" };
      }),
    );
    headers.forEach(function (h) {
      var sorted = h.c != null && h.c === key;
      var text = h.text + (sorted ? (dir < 0 ? " ↓" : " ↑") : "");
      var th = el(
        "th",
        h.cls + (sorted ? " s" : ""),
        inline || h.c == null ? text : "",
      );
      th.setAttribute("scope", "col");
      if (h.c != null)
        th.setAttribute(
          "aria-sort",
          sorted ? (dir < 0 ? "descending" : "ascending") : "none",
        );
      if (!inline && h.c != null) {
        // A real button, so the sort is reachable from the keyboard.
        var sortBtn = el("button", "sc-sort", text);
        sortBtn.type = "button";
        sortBtn.addEventListener("click", function () {
          ui.sort = { key: h.c, dir: sorted ? -dir : h.c === "" ? 1 : -1 };
          render(last);
        });
        th.appendChild(sortBtn);
      }
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    tbl.appendChild(thead);
    // each column's sign by the one rule; the bar's extent over the rows shown
    var kj = whole.columns.indexOf(key);
    var ext = 1;
    var signed = {};
    cols.forEach(function (c) {
      signed[c] = outputSigned(env, c);
    });
    // the bar diverges only when a shown value is negative
    var diverge =
      kj > 0 &&
      shown.some(function (r) {
        return typeof r[kj] === "number" && r[kj] < 0;
      });
    if (kj > 0)
      ext =
        Math.max.apply(
          null,
          shown.map(function (r) {
            return typeof r[kj] === "number" ? Math.abs(r[kj]) : 0;
          }),
        ) || 1;
    var tb = el("tbody");
    shown.forEach(function (r, i) {
      var tr = el("tr");
      tr.appendChild(el("td", "rk", String(i + 1)));
      var td0 = el("td", "t");
      td0.appendChild(document.createTextNode(String(r[0])));
      tr.appendChild(td0);
      cols.forEach(function (c) {
        var j = whole.columns.indexOf(c);
        var v = r[j];
        var td = el("td", "n" + (c === key ? " s" : ""));
        var text = fmtValue(env, v, labelOf(env, c).unit, signed[c], true);
        if (c === key && kj > 0) {
          var cell = el("span", "sc-cell");
          var bar = el("span", "sc-bar" + (diverge ? " div" : ""));
          var fill = el("i", typeof v === "number" && v < 0 ? "neg" : "");
          var share = typeof v === "number" ? Math.abs(v) / ext : 0;
          if (diverge) {
            // diverging: zero in the middle, negatives grow leftwards
            fill.style.width = (share * 50).toFixed(1) + "%";
            fill.style.left = (v < 0 ? 50 - share * 50 : 50).toFixed(1) + "%";
          } else fill.style.width = (share * 100).toFixed(0) + "%";
          bar.appendChild(fill);
          cell.appendChild(bar);
          cell.appendChild(el("span", "sc-val", text));
          td.appendChild(cell);
        } else td.appendChild(document.createTextNode(text));
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    wrap.appendChild(tbl);
    body.appendChild(wrap);
    if (set.size > shown.length) {
      var more = tpl(env, "more_in_keel", { k: set.size - shown.length });
      if (more) body.appendChild(el("p", "sc-more muted", more));
    }
    rangeLine(body, env, shown.length);
    if (inline) return;
    var note = tpl(env, "sorted_within", { n: rows.length });
    if (note) body.appendChild(el("p", "sc-cap muted", note));
  }

  H.ready(function (env) {
    last = env || {};
    render(last);
  });
  H.onResize(function () {
    if (last) render(last);
  });
})();
