/* Screen card: the SAVED VIEW, drawn small for MCP hosts (spec 06 §D3.3,
   §D3.5; INV-53; Q-3635), on the chat cards' face (D-420, D-420c; SCREENS-CARD,
   lane-scratch/SCREENS-CARD/NOTE.md). It presents; the assistant's reply
   interprets. No editorial sentence, no plan words, a ranking is data.

   Inline (the host's budget: one hero and a few data points, two actions):
   - the head: the screen's name, then one muted line (the kind, the version,
     the cadence and the data's time; a refresh in trouble says so instead);
   - Table: the top five of the view's set as rows (rank, coin, a data bar in
     the coin's identity colour, the value), the first emphasised;
   - Lines: the top five lines in their identity colours over every other coin
     as a grey field, end-labelled, a line that leaves the band capped at the
     edge with its value written there;
   - Scatter: the view's x / y, the top five coloured and named, the rest grey;
   - the baseline: one strip with every coin with a value as a tick on one
     axis, zero marked, the ends named, the top five in their colours;
   - the count: "15 of 179 coins · 10 more in Expand", and what entered and
     left the set since the last bar as chips;
   - the actions: the adapter's View in Keel and Expand, unfilled.

   Expand (fullscreen): the head; the published views as tabs; Table: every
   row of the set with every declared column, ties marked, sortable; Lines:
   Show 5 / 10 / All, the top five coloured and the rest grey (past the six
   identity colours a shown line is ink grey, never a new colour), the scrub
   with its readout in words; Scatter: the axis pickers; then View in Keel at
   the selected view, and Test as a strategy.

   Identity colour: rank r of the view's set takes `KeelHost.palette().ids[r]`
   (the face's validated palette: the accent first) for r < 6, the same coin
   the same colour in every row, tick and line; every other coin is the grey
   field. Rules are the host's tertiary border (`--rule`), never white.

   An envelope without a result (a saved view, a run in flight) draws the head
   and one line; a plan limit its sentences and no link; an error one line.

   Numbers: one formatter (`fmtValue`) and one sign rule (`outputSigned`,
   keel-app's `isSigned`) for every column, axis and line: a column prints "+"
   above zero when its output can go below zero (Q-3640).

   Every word comes from the envelope: `copy.*` (served by keel-api) and
   `copy.card.*` (the card's templates). A template the envelope does not
   serve renders nothing: this file carries no English. */
(function () {
  "use strict";
  var H = window.KeelHost;
  var el = H.el;
  var NS = "http://www.w3.org/2000/svg";
  var MINUS = "−";
  var SEP = " · ";
  // The inline hero: the first five of the set (RESEARCH-2 §3.1).
  var TOP = 5;
  // Identity colours: the face palette's first five slots, never cycled (the
  // sixth, violet, holds 2.6:1 on Claude's dark ground; ranks past five are
  // ink grey, a chosen coin drawn heavier).
  var IDS = 5;
  // A run's freshness states that need no words: any other state (a refresh
  // late, incomplete, stopped, aged out) shows its served freshness words.
  var CALM = ["live", "static"];
  // A plan limit's codes (mcp-conversion D-8; `_channels.PLAN_LIMIT_CODES`).
  var QUOTA = [
    "quota_exhausted",
    "quota_cap_reached",
    "plan_feature_unavailable",
  ];
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
  /** The column header: the label, then the unit once when it is not said.
      With the served `label_unit` and `unit_labels` ("{label} ({unit})",
      "% a year") the unit reads as a unit; else the legacy "label, a year". */
  function headerLabel(env, name) {
    var lab = labelOf(env, name);
    var word = unitWord(env, lab.unit);
    if (labelStatesUnit(lab.label, word)) return lab.label;
    var units = cardCopy(env).unit_labels || {};
    var shown = typeof units[lab.unit] === "string" ? units[lab.unit] : "";
    var both = shown
      ? tpl(env, "label_unit", { label: lab.label, unit: shown })
      : null;
    return both || lab.label + ", " + word;
  }
  function joinWords(parts) {
    return parts
      .filter(function (p) {
        return typeof p === "string" && p;
      })
      .join(SEP);
  }

  // ---------- numbers (S1 `sfmt`/`pct`/`ssPrice` on Keel's storage units) ----------
  /** The one sign rule every branch prints (keel-app's `sign` + `fixed`):
      U+2212 below zero, "+" above zero when the column is signed, nothing
      on a value that rounds to zero at `d` decimals. */
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
      A sign on signed columns, one decimal for %, U+2212. `bare` leaves the
      unit word off (a header or label line carries it). A rate past 1,000 %
      groups its digits ("+2,500%"). */
  function fmtValue(env, v, unit, signed, bare) {
    if (v == null || !isFinite(v)) return "—";
    var suffix = bare ? "" : unitWord(env, unit);
    var out;
    if (isRate(unit)) {
      var p = unit === "percent" ? v : v * 100;
      out =
        Math.abs(p) >= 1000
          ? signOf(p, signed) + Math.round(Math.abs(p)).toLocaleString("en-US")
          : fixed(p, 1, signed);
      out += "%";
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
  /** A bar instant in words, UTC, as the server's as-of words are
      ("Thu 5 Nov 13:00 UTC"); never an ISO stamp (Q-7925). */
  function whenWords(ms, weekday) {
    if (ms == null || !isFinite(ms)) return "";
    var d = new Date(ms);
    var opts = { timeZone: "UTC", day: "numeric", month: "short" };
    if (weekday) opts.weekday = "short";
    var day = d.toLocaleDateString("en-GB", opts).replace(",", "");
    var time = d.toLocaleTimeString("en-GB", {
      timeZone: "UTC",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    return day + " " + time + " UTC";
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
  /** The output's declared bounds (`manifest.outputs[].bounds`), or null. */
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
  /** Whether a column prints "+" above zero: keel-app's `isSigned`. The
      declared bounds; else `range` (every coin with a value); else the whole
      `latest` table; else a negative anywhere the card can see; else the
      unit's nature. Never the rows shown alone (Q-3640). */
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
  /** The view's filter, read off the envelope (contract items 2 and 7). */
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
  /** What entered and left the set since the last bar (`passing.entered`,
      `passing.left`, ranked, ≤ 5 each; `counts` the whole numbers). */
  function moves(env) {
    var p = env.passing;
    if (!p || typeof p !== "object") return null;
    var counts = p.counts || {};
    var entered = Array.isArray(p.entered) ? p.entered.map(String) : [];
    var left = Array.isArray(p.left) ? p.left.map(String) : [];
    if (!entered.length && !left.length) return null;
    return {
      entered: entered,
      left: left,
      enteredMore: Math.max(0, (counts.entered || 0) - entered.length),
      leftMore: Math.max(0, (counts.left || 0) - left.length),
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
  /** The hero lines ranked by their last value in the run's order. */
  function rankSeries(env, series) {
    var desc = ((env.manifest || {}).order || "desc") !== "asc";
    return series.slice().sort(function (a, b) {
      if (a.end == null && b.end == null) return 0;
      if (a.end == null) return 1;
      if (b.end == null) return -1;
      return desc ? b.end - a.end : a.end - b.end;
    });
  }
  /** The whole passing set at the cursor (`hero.passing`, L2), or null. */
  function passingSet(env) {
    var p = (env.hero || {}).passing;
    if (!Array.isArray(p)) return null;
    // The stored list is bounded (the first 50 by name). When it is full and
    // more coins pass than it holds, it is not the passing set.
    var count = filterInfo(env).count;
    if (count != null && p.length >= 50 && count > p.length) return null;
    return p.map(String);
  }
  /** The author's default graph among the kinds the card draws. */
  function defaultKind(env) {
    var g = env.graphs || {};
    var d = (g.default_channels || {}).kind || g.default;
    return d === "Scatter" || d === "Table" ? d : "Lines";
  }
  /** What the default view declares: its columns, its sort column and
      direction. Falls back to the ranked output over every shipped column. */
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
  /** The rows the view shows: the passing set (or the first `count` by the
      sort) when it filters, every row otherwise; sorted by the view's sort.
      `size` is how many coins are in the set. */
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
  /** Identity: coin → its rank in the view's set (0-based), for the first
      IDS ranks; the same coin the same colour everywhere on the card. */
  function identity(env) {
    var plan = viewPlan(env);
    var set = setRows(env, plan);
    var order = set.rows.map(function (r) {
      return String(r[0]);
    });
    if (!order.length && env.hero && env.hero.assets)
      order = rankSeries(env, heroSeries(env.hero)).map(function (s) {
        return s.asset;
      });
    var slot = {};
    order.slice(0, IDS).forEach(function (a, i) {
      slot[a] = i;
    });
    return slot;
  }
  /** The palette's identity colours on the card's root, per host theme. */
  function applyIds() {
    var ids = (typeof H.palette === "function" ? H.palette().ids : []) || [];
    var s = document.documentElement.style;
    for (var i = 0; i < IDS; i++)
      if (ids[i]) s.setProperty("--sc-id-" + i, ids[i]);
  }
  function idVar(slot) {
    return slot == null ? null : "var(--sc-id-" + slot + ")";
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
  /** Text width estimate at 12 px (tabular figures): for layout only. */
  function textW(s) {
    return String(s).length * 6.6;
  }

  /** The Lines picture (Q-3635, Q-7923, Q-7924): the field under, the
      shown lines on top, held to the shown lines' pooled 1st–99th
      percentile band so one spike cannot flatten the rest. A line that
      leaves the band is drawn to the band's edge and capped there (a dashed
      run along the edge), and its farthest value is written at the edge in
      the line's colour: "↑ +2,500%". Zero dashed when the plot crosses it,
      y ticks left, time ticks under, end labels nudged 14 px apart; the
      isolated line kept and every other dimmed. */
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
      longest = Math.max(longest, textW(endText(s)));
    });
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
      gutter = Math.max(gutter, textW(tickText(v)));
    });
    var P = {
      l: 8 + gutter,
      r: labelled.length ? Math.min(160, 12 + longest) : 6,
      t: 10,
      b: 22,
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
    // a point is in the band when it maps inside the plot; one that does
    // not is drawn at the edge it left by
    var side = function (v) {
      var y = Y(v);
      return y < P.t ? -1 : y > P.t + ph ? 1 : 0;
    };
    var svg = svgEl("svg", {
      class: "sc-svg",
      width: W,
      height: Hh,
      viewBox: "0 0 " + W + " " + Hh,
      role: "img",
      "aria-label": opts.aria || null,
      // the plot's top and bottom: every drawn point sits between (Q-7924)
      "data-plot": P.t + " " + (P.t + ph),
    });
    tick.ticks.forEach(function (v) {
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
        x: P.l - 6,
        y: y,
        "text-anchor": "end",
      });
      t.textContent = tickText(v);
      svg.appendChild(t);
    });
    var clamp = function (v) {
      return Math.max(P.t, Math.min(P.t + ph, Y(v)));
    };
    model.field.forEach(function (s) {
      svg.appendChild(
        svgEl("path", {
          class: "sc-field",
          d: pathD(
            s.v.map(function (y, j) {
              return y == null ? null : [X(j), clamp(y)];
            }),
          ),
        }),
      );
    });
    if (model.zero != null && lo < model.zero && hi > model.zero)
      svg.appendChild(
        svgEl("line", {
          class: "sc-zero",
          x1: P.l,
          x2: P.l + pw,
          y1: Y(model.zero),
          y2: Y(model.zero),
        }),
      );
    var iso = model.iso;
    var marks = [];
    model.shown
      .slice()
      .reverse()
      .forEach(function (s) {
        var colour = model.colour(s.asset);
        var cls = "sc-line" + (colour ? "" : " sc-ink");
        var dim = iso && iso !== s.asset ? " sc-dim" : "";
        // the line in the band; out-of-band runs break it
        var inBand = s.v.map(function (y, j) {
          return y == null || side(y) ? null : [X(j), Y(y)];
        });
        // the capped runs: each out-of-band point at its edge, joined to its
        // in-band neighbours, dashed
        var capped = [];
        var peak = null;
        s.v.forEach(function (y, j) {
          if (y == null || !side(y)) return;
          var prev = s.v[j - 1];
          if (j > 0 && prev != null && !side(prev))
            capped.push(null, [X(j - 1), Y(prev)]);
          else if (j === 0 || prev == null) capped.push(null);
          capped.push([X(j), clamp(y)]);
          var next = s.v[j + 1];
          if (next != null && !side(next)) capped.push([X(j + 1), Y(next)]);
          if (!peak || Math.abs(y) > Math.abs(peak.v))
            peak = { v: y, j: j, up: side(y) < 0 };
        });
        var width = iso === s.asset ? 2.8 : s.first ? 2.4 : 1.6;
        var stroke = colour || "currentColor";
        g(
          "path",
          {
            class: "ml " + cls + dim,
            d: pathD(inBand),
            stroke: stroke,
            "stroke-width": width,
            "data-asset": s.asset,
          },
          svg,
        );
        if (capped.length)
          g(
            "path",
            {
              class: "ml sc-cap-run " + cls + dim,
              d: pathD(capped),
              stroke: stroke,
              "stroke-width": Math.max(1.4, width - 0.6),
              "data-asset": s.asset,
            },
            svg,
          );
        g(
          "path",
          {
            class: "sc-hit",
            d: pathD(
              s.v.map(function (y, j) {
                return y == null ? null : [X(j), clamp(y)];
              }),
            ),
            "data-asset": s.asset,
          },
          svg,
        );
        if (peak) marks.push({ s: s, peak: peak, colour: stroke, dim: dim });
      });
    // the marker that reads: an arrow and the value, at the edge, inside
    // the plot, in the line's colour (Q-7924)
    marks.forEach(function (m) {
      var label =
        (m.peak.up ? "↑ " : "↓ ") +
        fmtValue(env, m.peak.v, unit, model.signed, true);
      // beside the capped run (right of it, or left when the plot ends)
      var px = X(m.peak.j);
      var right = px + 10 + textW(label) <= P.l + pw;
      var t = svgEl("text", {
        class: "sc-clip" + m.dim,
        x: right ? px + 10 : px - 10,
        y: m.peak.up ? P.t + 8 : P.t + ph - 8,
        "text-anchor": right ? "start" : "end",
        fill: m.colour,
        "data-asset": m.s.asset,
      });
      t.textContent = label;
      svg.appendChild(t);
    });
    if (opts.cursor != null) {
      var cx = X(opts.cursor);
      svg.appendChild(
        svgEl("line", {
          class: "sc-cursor",
          x1: cx,
          x2: cx,
          y1: P.t,
          y2: P.t + ph,
        }),
      );
    }
    var pl = [];
    labelled.forEach(function (s) {
      var j = lastIdx(s.v);
      if (j >= 0) {
        var v = s.v[j];
        pl.push({ y: clamp(v), x: X(j), s: s, off: side(v) });
      }
    });
    pl.sort(function (p, q) {
      return p.y - q.y;
    });
    // At most one label per 14 px of plot. Spread downwards, then pull back
    // up from the bottom edge, so a label stays near its line.
    var cap = Math.max(1, Math.floor(ph / 14));
    if (pl.length > cap) pl = pl.slice(0, cap);
    for (var k = 1; k < pl.length; k++)
      if (pl[k].y - pl[k - 1].y < 14) pl[k].y = pl[k - 1].y + 14;
    if (pl.length) {
      var lastI = pl.length - 1;
      pl[lastI].y = Math.min(pl[lastI].y, P.t + ph);
      for (var m2 = lastI - 1; m2 >= 0; m2--)
        pl[m2].y = Math.min(pl[m2].y, pl[m2 + 1].y - 14);
      pl.forEach(function (p) {
        p.y = Math.max(p.y, P.t + 6);
      });
    }
    pl.forEach(function (p) {
      var colour = model.colour(p.s.asset);
      var t = svgEl("text", {
        class:
          "sc-end" +
          (p.s.first ? " sc-first" : "") +
          (colour ? "" : " sc-ink") +
          (iso && iso !== p.s.asset ? " sc-dim" : ""),
        x: p.x + 6,
        y: p.y,
        fill: colour,
        "data-asset": p.s.asset,
      });
      t.textContent = (p.off < 0 ? "↑ " : p.off > 0 ? "↓ " : "") + endText(p.s);
      svg.appendChild(t);
    });
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
  function g(tag, attrs, into) {
    var n = svgEl(tag, attrs);
    into.appendChild(n);
    return n;
  }

  // ---------- the face ----------
  function faceFrame(layout) {
    var b = document.body;
    if (b) {
      b.setAttribute("data-face", "1");
      b.setAttribute("data-layout", layout);
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
  }
  /** The head: the screen's name, then one muted line. */
  function faceHead(body, env, full) {
    var view = env.view || {};
    var screen = env.screener || {};
    var copy = env.copy || {};
    var head = el("div", "sc-head");
    var name = view.name || screen.name || "";
    var t = el("h2", "sc-title", name);
    if (name) t.title = name;
    head.appendChild(t);
    var version = view.version != null ? view.version : screen.version;
    var asOfMs =
      view.as_of != null
        ? view.as_of
        : copy.as_of && typeof copy.as_of === "object"
          ? copy.as_of.as_of_ms
          : null;
    // the data's time: the served as-of words when the headline is older
    // than the run's last bar (they name the input that stopped), else the
    // run's as-of in the served template
    var when =
      copy.as_of && typeof copy.as_of === "object" && copy.as_of.words
        ? copy.as_of.words
        : asOfMs != null
          ? tpl(env, "as_of", { time: whenWords(asOfMs) })
          : null;
    var freshness = view.freshness || env.freshness;
    var trouble =
      typeof freshness === "string" &&
      freshness &&
      CALM.indexOf(freshness) < 0 &&
      typeof copy.freshness === "string";
    var meta = joinWords([
      tpl(env, "kind"),
      version != null && version !== ""
        ? "v" + String(version).replace(/^v/i, "")
        : null,
      copy.clock,
      trouble ? copy.freshness : when,
    ]);
    if (meta) {
      var m = el("p", "sc-meta", meta);
      m.title = meta;
      head.appendChild(m);
    }
    body.appendChild(head);
  }
  /** The count: "15 of 179 coins · 10 more in Expand" (or "+N more in Keel"
      where the host offers no fullscreen), then what moved, as chips. */
  function footLine(body, env, shown, full) {
    var plan = viewPlan(env);
    var set = setRows(env, plan);
    var fi = set.filter;
    var foot = el("div", "sc-foot");
    var count = fi.filtered
      ? tpl(env, "pass_now", { passing: fi.count, of: fi.total })
      : fi.total != null
        ? tpl(env, "coins", { n: fi.total })
        : null;
    var more = null;
    var rest = set.size - shown;
    if (!full && rest > 0)
      more =
        H.availableDisplayModes.indexOf("fullscreen") >= 0
          ? tpl(env, "more_in_expand", { k: rest })
          : tpl(env, "more_in_keel", { k: rest });
    var words = joinWords([count, more]);
    if (words) foot.appendChild(el("span", "sc-count", words));
    var mv = moves(env);
    if (mv) {
      var chips = el("span", "sc-moves");
      var lab = tpl(env, "since_last");
      if (lab) chips.appendChild(el("span", "sc-moves-l", lab));
      mv.entered.forEach(function (a) {
        chips.appendChild(chip(env, a, true));
      });
      if (mv.enteredMore)
        chips.appendChild(el("span", "sc-chip sc-in", "+" + mv.enteredMore));
      mv.left.forEach(function (a) {
        chips.appendChild(chip(env, a, false));
      });
      if (mv.leftMore)
        chips.appendChild(el("span", "sc-chip sc-out", "+" + mv.leftMore));
      foot.appendChild(chips);
    }
    if (foot.childNodes.length) body.appendChild(foot);
  }
  function chip(env, asset, entered) {
    var c = el(
      "span",
      "sc-chip " + (entered ? "sc-in" : "sc-out"),
      (entered ? "▲ " : "▼ ") + asset,
    );
    var words = tpl(env, entered ? "entered_asset" : "left_asset", {
      asset: asset,
    });
    if (words) {
      c.title = words;
      c.setAttribute("aria-label", words);
    }
    return c;
  }

  /** The top rows (the inline hero of a Table view): rank, coin, a data bar
      in the coin's identity colour, the value; the first emphasised. A tie
      that the cutoff of the five splits shows its first row and the rest as
      one chip. */
  function heroRows(body, env, slot) {
    var plan = viewPlan(env);
    var set = setRows(env, plan);
    if (!set.rows.length || set.ki < 0) return 0;
    var lab = labelOf(env, plan.sort);
    var signed = outputSigned(env, plan.sort);
    var ki = set.ki;
    var rows = set.rows.slice(0, TOP);
    var tie = set.filter.tie;
    var tied = 0;
    if (tie && tie.count > 1 && typeof tie.value === "number") {
      var at = rows.filter(function (r) {
        return r[ki] === tie.value;
      });
      if (at.length > 1) {
        tied = tie.count - 1;
        var firstAt = rows.indexOf(at[0]);
        rows = rows.slice(0, firstAt + 1);
      }
    }
    var entered = {};
    ((env.passing || {}).entered || []).forEach(function (a) {
      entered[String(a)] = true;
    });
    body.appendChild(el("p", "sc-label", headerLabel(env, plan.sort)));
    var ext = Math.max.apply(
      null,
      rows.map(function (r) {
        return typeof r[ki] === "number" ? Math.abs(r[ki]) : 0;
      }),
    );
    if (!(ext > 0)) ext = 1;
    var diverge = rows.some(function (r) {
      return typeof r[ki] === "number" && r[ki] < 0;
    });
    var list = el("ol", "sc-rows");
    rows.forEach(function (r, i) {
      var asset = String(r[0]);
      var v = r[ki];
      var li = el("li", "sc-r" + (i === 0 ? " sc-first" : ""));
      li.setAttribute("data-asset", asset);
      li.appendChild(el("span", "sc-rk", String(i + 1)));
      var name = el("span", "sc-k", asset);
      if (entered[asset]) {
        var nw = tpl(env, "new");
        if (nw) name.appendChild(el("span", "sc-new", nw));
      }
      li.appendChild(name);
      var bar = el("span", "sc-bar" + (diverge ? " sc-div" : ""));
      var fill = el("i", "");
      var share = typeof v === "number" ? Math.abs(v) / ext : 0;
      if (diverge) {
        fill.style.width = (share * 50).toFixed(1) + "%";
        fill.style.left = (v < 0 ? 50 - share * 50 : 50).toFixed(1) + "%";
      } else fill.style.width = (share * 100).toFixed(1) + "%";
      var c = idVar(slot[asset]);
      if (c) fill.style.background = c;
      bar.appendChild(fill);
      li.appendChild(bar);
      li.appendChild(
        el("span", "sc-v", fmtValue(env, v, lab.unit, signed, true)),
      );
      list.appendChild(li);
    });
    body.appendChild(list);
    if (tied > 0) {
      var note = tpl(env, "tie_more", {
        n: tied,
        value: fmtValue(env, tie.value, lab.unit, signed, true),
      });
      if (note) body.appendChild(el("p", "sc-tie", note));
    }
    return rows.length + tied;
  }

  /** The baseline (Q-7922's strip): every coin with a value as a tick on one
      axis (the whole `latest` column when shipped, else the served range's
      ends), zero marked, the ends named, the top five in their colours and
      taller. Draws nothing without a range to place it on. */
  function strip(body, env, slot, width, inPanel) {
    var plan = viewPlan(env);
    var name = plan.sort;
    var r = env.range && env.range.output === name ? env.range : null;
    var whole =
      env.latest && env.latest.rows && env.latest.rows.length
        ? env.latest
        : null;
    var vals = [];
    if (whole) {
      var ci = whole.columns.indexOf(name);
      if (ci > 0)
        whole.rows.forEach(function (row) {
          if (typeof row[ci] === "number" && isFinite(row[ci]))
            vals.push({ a: String(row[0]), v: row[ci] });
        });
    }
    var lo = r && r.low ? r.low : null;
    var hi = r && r.high ? r.high : null;
    if (vals.length) {
      var sorted = vals.slice().sort(function (p, q) {
        return p.v - q.v;
      });
      if (!lo) lo = { asset: sorted[0].a, value: sorted[0].v };
      if (!hi)
        hi = {
          asset: sorted[sorted.length - 1].a,
          value: sorted[sorted.length - 1].v,
        };
    }
    if (!lo || !hi || !(hi.value > lo.value)) return;
    var lab = labelOf(env, name);
    var signed = outputSigned(env, name);
    var wrap = el("div", "sc-stripw");
    body.appendChild(wrap);
    var W = Math.max(240, wrap.clientWidth || width);
    var Hh = 44;
    var padX = 2;
    var X = function (v) {
      return padX + ((v - lo.value) / (hi.value - lo.value)) * (W - 2 * padX);
    };
    var svg = svgEl("svg", {
      class: "sc-strip",
      width: W,
      height: Hh,
      viewBox: "0 0 " + W + " " + Hh,
      role: "img",
      "aria-label":
        tpl(env, "range", {
          count: r ? r.count : vals.length,
          low: fmtValue(env, lo.value, lab.unit, signed, true),
          low_asset: lo.asset,
          high: fmtValue(env, hi.value, lab.unit, signed, true),
          high_asset: hi.asset,
          label: headerLabel(env, name),
        }) || null,
    });
    var axisY = 22;
    g(
      "line",
      { class: "sc-axis-l", x1: padX, x2: W - padX, y1: axisY, y2: axisY },
      svg,
    );
    var tops = [];
    vals.forEach(function (p) {
      if (slot[p.a] != null && slot[p.a] < TOP) tops.push(p);
      else
        g(
          "line",
          {
            class: "sc-tk",
            x1: X(p.v),
            x2: X(p.v),
            y1: axisY - 6,
            y2: axisY + 6,
          },
          svg,
        );
    });
    if (lo.value < 0 && hi.value > 0) {
      var x0 = X(0);
      g(
        "line",
        { class: "sc-zero", x1: x0, x2: x0, y1: axisY - 11, y2: axisY + 11 },
        svg,
      );
      var below =
        r && r.below_zero > 0
          ? tpl(env, "range_below", { n: r.below_zero })
          : null;
      if (below && x0 - 8 - textW(below) > 0) {
        var bt = svgEl("text", {
          class: "sc-sl",
          x: x0 - 6,
          y: 8,
          "text-anchor": "end",
        });
        bt.textContent = below;
        svg.appendChild(bt);
      }
      var zt = svgEl("text", { class: "sc-sl", x: x0 + 5, y: 8 });
      zt.textContent = "0";
      svg.appendChild(zt);
    }
    // the top five on top, in their colours, the first heaviest
    tops
      .sort(function (p, q) {
        return slot[q.a] - slot[p.a];
      })
      .forEach(function (p) {
        g(
          "line",
          {
            class: "sc-tk-id",
            x1: X(p.v),
            x2: X(p.v),
            y1: axisY - 10,
            y2: axisY + 10,
            stroke: idVar(slot[p.a]),
            "stroke-width": slot[p.a] === 0 ? 3 : 2,
            "data-asset": p.a,
          },
          svg,
        );
      });
    var ends = [
      [lo, 0, "start"],
      [hi, W, "end"],
    ];
    ends.forEach(function (e) {
      var t = svgEl("text", {
        class: "sc-sl sc-end-l",
        x: e[1],
        y: Hh - 3,
        "text-anchor": e[2],
      });
      t.textContent =
        e[2] === "start"
          ? fmtValue(env, e[0].value, lab.unit, signed, true) + " " + e[0].asset
          : e[0].asset +
            " " +
            fmtValue(env, e[0].value, lab.unit, signed, true);
      svg.appendChild(t);
    });
    var across = inPanel
      ? null
      : tpl(env, "across", { count: r ? r.count : vals.length });
    if (across) {
      var ct = svgEl("text", {
        class: "sc-sl sc-across",
        x: W / 2,
        y: Hh - 3,
        "text-anchor": "middle",
      });
      ct.textContent = across;
      // only where it clears both end labels
      var half = textW(across) / 2;
      var leftEnd =
        textW(
          fmtValue(env, lo.value, lab.unit, signed, true) + " " + lo.asset,
        ) + 12;
      var rightEnd =
        W -
        textW(
          hi.asset + " " + fmtValue(env, hi.value, lab.unit, signed, true),
        ) -
        12;
      if (W / 2 - half > leftEnd && W / 2 + half < rightEnd)
        svg.appendChild(ct);
    }
    wrap.appendChild(svg);
  }

  // ---------- the card ----------
  function render(env) {
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    body.removeAttribute("aria-busy");
    applyIds();
    var b = document.body;
    if (b) {
      b.removeAttribute("data-no-expand");
      b.removeAttribute("data-owns-link");
    }
    var view = env.view || {};
    if (env.code && typeof env.message === "string" && !env.view) {
      renderRefusal(body, env);
      return finish(env, false);
    }
    var full = H.displayMode() === "fullscreen";
    if (view.size === "receipt" || !env.manifest) {
      // No result to draw (a saved view, a run in flight): the head and one
      // line, never an empty body.
      faceFrame("receipt");
      faceHead(body, env, false);
      var run = env.run || {};
      var copy = env.copy || {};
      var words =
        run.error_words ||
        run.status_words ||
        copy.freshness ||
        copy.description;
      if (words) body.appendChild(el("p", "sc-status", words));
      if (b) b.setAttribute("data-no-expand", "1");
      return finish(env, false);
    }
    var runKey = (env.run || {}).run_id || view.object || "";
    if (ui.key !== runKey)
      ui = { key: runKey, iso: null, tab: null, top: null, at: null };
    var slot = identity(env);
    if (full) renderFull(body, env, slot);
    else renderInline(body, env, slot);
    finish(env, full);
  }
  /** The link row: the adapter's View in Keel (at the selected view in
      Expand) and Expand inline; in Expand, Test as a strategy after it. */
  function finish(env, full) {
    var linkEnv = env;
    if (full && env.hero_url) {
      linkEnv = {};
      for (var k in env) linkEnv[k] = env[k];
      linkEnv.hero_url = viewLink(env, ui.tab);
    }
    if (typeof H.renderLinkRow === "function") H.renderLinkRow(linkEnv);
    if (full) {
      var row = document.getElementById("link-row");
      var test = (env.actions || []).filter(function (a) {
        return a && a.url && a.url !== env.hero_url && a.label;
      })[0];
      if (row && !row.hidden && test) {
        // Through the host bridge (ui/open-link, openExternal): a sandbox
        // without allow-popups blocks a plain target=_blank link.
        var a = el("a", "button-link button-quiet sc-test", test.label);
        a.href = test.url;
        a.title = test.url;
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          H.openLink(test.url);
        });
        row.appendChild(a);
      }
    }
    H.notifySize();
  }
  /** The screen's page at a published view: the default view is the link
      itself; another kind's first view adds `v` (the app's view key) when
      the envelope names its views (`graphs.views`). */
  function viewLink(env, kind) {
    var url = env.hero_url;
    if (!url || !kind || kind === defaultKind(env)) return url;
    var views = (env.graphs || {}).views;
    if (!Array.isArray(views)) return url;
    var hit = views.filter(function (v) {
      return v && v.kind === kind && typeof v.id === "string";
    })[0];
    if (!hit) return url;
    return (
      url +
      (url.indexOf("?") >= 0 ? "&" : "?") +
      "v=" +
      encodeURIComponent(hit.id)
    );
  }

  /** A refusal. A plan limit is one row of its served words (plans spec 02
      §C3.2, research D-422: a limit names the plans in text, names only;
      mcp-conversion D-12: no link, price or badge), its first clause set
      bold; no button and no Expand. Any other refusal: its one line. */
  function renderRefusal(body, env) {
    var b = document.body;
    if (b) {
      b.setAttribute("data-face", "1");
      b.setAttribute("data-no-expand", "1");
      b.setAttribute("data-owns-link", "1");
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
    var detail = env.detail && typeof env.detail === "object" ? env.detail : {};
    var example =
      env.example && typeof env.example === "object" ? env.example : {};
    var limit =
      env.code === "SCREENER_REFRESH_BELOW_FLOOR" ||
      (env.code === "insufficient_entitlements" &&
        (QUOTA.indexOf(detail.code) >= 0 ||
          QUOTA.indexOf(example.code) >= 0)) ||
      QUOTA.indexOf(env.code) >= 0;
    if (b) b.setAttribute("data-layout", limit ? "limit" : "error");
    var text = String(env.message);
    if (!limit) {
      body.appendChild(el("p", "sc-err", text));
      return;
    }
    var row = el("p", "sc-limit");
    var cut = text.search(/;\s|\.\s/);
    if (cut > 0) {
      row.appendChild(el("b", "sc-limit-h", text.slice(0, cut + 1)));
      row.appendChild(document.createTextNode(" "));
      row.appendChild(el("span", "sc-limit-r", text.slice(cut + 2)));
    } else row.appendChild(el("b", "sc-limit-h", text));
    body.appendChild(row);
  }

  function renderInline(body, env, slot) {
    var kind = defaultKind(env);
    faceFrame(kind.toLowerCase());
    faceHead(body, env, false);
    var width = H.cardWidth() || 600;
    var inner = Math.max(240, width - (width < 480 ? 24 : 32));
    var shown = 0;
    if (kind === "Table") {
      shown = heroRows(body, env, slot);
      if (shown) strip(body, env, slot, inner);
      else emptyState(body, env, slot, inner);
    } else if (kind === "Scatter") {
      shown = scatter(body, env, slot, true) || 0;
    } else if (env.hero && env.hero.n) {
      shown = lines(body, env, slot, "inline");
    } else {
      shown = heroRows(body, env, slot);
      if (shown) strip(body, env, slot, inner);
    }
    footLine(body, env, shown, false);
    if (!shown && document.body)
      document.body.setAttribute("data-no-expand", "1");
  }
  /** No coin in the set: the rule that decides it (the view's served
      sentence) and the baseline, so the reader sees where every coin sits. */
  function emptyState(body, env, slot, inner) {
    var desc = (env.copy || {}).description;
    if (desc) body.appendChild(el("p", "sc-desc", desc));
    // no coin is in the set: every coin is the field, none carries a colour
    strip(body, env, {}, inner);
  }

  // ---------- Lines ----------
  function lineModel(env, slot, full) {
    var hero = env.hero;
    var series = rankSeries(env, heroSeries(hero));
    var pass = passingSet(env);
    var all = series;
    if (pass && filterInfo(env).filtered) {
      var inPass = {};
      pass.forEach(function (a) {
        inPass[a] = true;
      });
      all = series
        .filter(function (s) {
          return inPass[s.asset];
        })
        .concat(
          series.filter(function (s) {
            return !inPass[s.asset];
          }),
        );
    }
    var top = full ? (ui.top != null ? ui.top : TOP) : TOP;
    var shownN = Math.min(top, all.length);
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
    shown.forEach(function (s, i) {
      s.first = i === 0;
    });
    var field = all.filter(function (s) {
      return shown.indexOf(s) < 0;
    });
    var lab = labelOf(env, hero.output);
    return {
      all: all,
      shown: shown,
      field: field,
      n: hero.n,
      zero: hero.relative === "rebase_100" ? 100 : 0,
      iso: ui.iso,
      signed: hero.relative === "difference" || outputSigned(env, hero.output),
      unit: lab.unit,
      rebased: hero.relative === "rebase_100",
      log: hero.y_scale === "log",
      colour: function (asset) {
        // identity by the line's own rank among those drawn, never cycled
        var i = shown.findIndex(function (s) {
          return s.asset === asset;
        });
        var r = slot[asset] != null ? slot[asset] : i;
        return r != null && r >= 0 && r < IDS ? idVar(r) : null;
      },
    };
  }
  /** The Lines picture: inline (the label and the chart), or a dashboard
      panel / its focused view (the chart, then the scrub and its readout). */
  function lines(host, env, slot, mode) {
    var full = mode !== "inline";
    var width = full
      ? host.clientWidth || H.cardWidth() || 600
      : H.cardWidth() || 600;
    var hero = env.hero || {};
    var m = lineModel(env, slot, full);
    if (!full)
      host.appendChild(el("p", "sc-label", headerLabel(env, hero.output)));
    else host.appendChild(el("p", "sc-plabel", headerLabel(env, hero.output)));
    var chart = el("div", "sc-chart");
    host.appendChild(chart);
    var labels = m.shown
      .map(function (s) {
        return s.asset;
      })
      .slice(0, full ? (width < 480 ? 6 : 20) : TOP);
    if (ui.iso && labels.indexOf(ui.iso) < 0) labels.push(ui.iso);
    var at = full && ui.at != null ? Math.min(ui.at, m.n - 1) : null;
    drawLines(chart, env, m, {
      width: Math.max(240, (chart.clientWidth || width) - 2),
      height:
        mode === "focus"
          ? width < 480
            ? 320
            : 440
          : mode === "panel"
            ? width < 480
              ? 260
              : 320
            : width < 480
              ? 168
              : 184,
      labels: labels,
      cursor: at,
      aria: headerLabel(env, hero.output),
    });
    chart.addEventListener("click", function (event) {
      var a =
        event.target &&
        event.target.getAttribute &&
        event.target.getAttribute("data-asset");
      if (a) {
        event.stopPropagation();
        isolate(a);
      }
    });
    if (!full) return m.shown.length;
    // the scrub over the window and its readout, in words (Q-7925)
    var slider = el("input", "sc-slider");
    slider.type = "range";
    slider.min = "0";
    slider.max = String(Math.max(0, m.n - 1));
    slider.value = String(at == null ? m.n - 1 : at);
    var scrub = tpl(env, "scrub");
    if (scrub) slider.setAttribute("aria-label", scrub);
    var lab = labelOf(env, hero.output);
    var read = el("p", "sc-read");
    var paint = function (j) {
      var vals = m.shown
        .filter(function (s) {
          return s.v[j] != null && (!ui.iso || s.asset === ui.iso);
        })
        .sort(function (p, q) {
          return q.v[j] - p.v[j];
        });
      read.textContent = "";
      read.appendChild(el("b", "sc-read-t", whenWords(msOf(env, j), true)));
      vals.slice(0, 6).forEach(function (s) {
        var it = el("span", "sc-read-i");
        var c = m.colour(s.asset);
        var sw = el("i", "sc-sw");
        if (c) sw.style.background = c;
        it.appendChild(sw);
        it.appendChild(
          document.createTextNode(
            s.asset +
              " " +
              fmtValue(env, s.v[j], m.rebased ? "" : lab.unit, m.signed, true),
          ),
        );
        read.appendChild(it);
      });
    };
    paint(Number(slider.value));
    slider.addEventListener("input", function () {
      paint(Number(slider.value));
    });
    slider.addEventListener("change", function () {
      ui.at = Number(slider.value);
      render(last);
    });
    host.appendChild(slider);
    host.appendChild(read);
    return m.shown.length;
  }
  function isolate(asset) {
    ui.iso = ui.iso === asset ? null : asset;
    if (last) render(last);
  }

  /** The published kinds the card draws (River is app-only), in spec 03's
      order; the author's default opens first. */
  function publishedKinds(env) {
    var g2 = env.graphs || {};
    var kinds = ["Table", "Lines", "Scatter"].filter(function (k) {
      return (g2.kinds || []).indexOf(k) >= 0;
    });
    if (!kinds.length) kinds = [defaultKind(env)];
    if (kinds.indexOf(defaultKind(env)) < 0) kinds.unshift(defaultKind(env));
    if (ui.tab == null || kinds.indexOf(ui.tab) < 0) ui.tab = defaultKind(env);
    return kinds;
  }
  function seg(items, current, onPick, cls) {
    var bar = el("div", "sc-seg" + (cls ? " " + cls : ""));
    bar.setAttribute("role", "group");
    items.forEach(function (it) {
      var bt = el("button", it.value === current ? "sc-on" : "", it.label);
      bt.type = "button";
      bt.setAttribute("aria-pressed", it.value === current ? "true" : "false");
      bt.addEventListener("click", function () {
        onPick(it.value);
      });
      bar.appendChild(bt);
    });
    return bar;
  }

  // ---------- Expand ----------
  /** Expand is a dashboard (D-420d): the controls up top (the view to
      focus, the table's sort, how many lines Show colours), then every view
      the screen has as a panel, visible together: the Lines chart large, the
      whole passing set as a table, the universe's distribution, a Scatter
      when the screen publishes one. Tapping a panel's name focuses it (it
      takes the page); the view control's first choice returns to all. */
  function renderFull(body, env, slot) {
    faceFrame("full");
    faceHead(body, env, true);
    var kinds = publishedKinds(env);
    var hasLines = !!(env.hero && env.hero.n && kinds.indexOf("Lines") >= 0);
    var hasScatter = kinds.indexOf("Scatter") >= 0;
    var panels = [];
    if (hasLines) panels.push("Lines");
    panels.push("Table");
    if (hasScatter) panels.push("Scatter");
    if (ui.focus && panels.indexOf(ui.focus) < 0) ui.focus = null;
    controls(body, env, panels, hasLines);
    // A table of several columns takes the page's width under the charts;
    // a one-column table sits beside them.
    var wide = viewPlan(env).columns.length > 1;
    var dash = el(
      "div",
      "sc-dash" + (ui.focus ? " sc-focused" : "") + (wide ? " sc-wide" : ""),
    );
    body.appendChild(dash);
    var show = function (k) {
      return !ui.focus || ui.focus === k;
    };
    var drawLines = function () {
      if (hasLines && show("Lines")) {
        var pl = panel(dash, env, "Lines", "Lines");
        lines(pl, env, slot, ui.focus ? "focus" : "panel");
      }
    };
    var drawTable = function () {
      if (show("Table")) {
        var pt = panel(dash, env, "Table", "Table");
        footLine(pt, env, 0, true);
        table(pt, env, slot);
      }
    };
    var drawDist = function () {
      if (!ui.focus || ui.focus === "Table" || ui.focus === "Lines") {
        var pd = panel(
          dash,
          env,
          null,
          tpl(env, "across", { count: rangeCount(env) }),
        );
        strip(pd, env, slot, pd.clientWidth || 600, true);
      }
    };
    var drawScatter = function () {
      if (hasScatter && show("Scatter")) {
        var ps = panel(dash, env, "Scatter", "Scatter");
        scatter(ps, env, slot, false);
      }
    };
    if (wide) {
      drawLines();
      drawScatter();
      drawDist();
      drawTable();
    } else {
      drawLines();
      drawTable();
      drawDist();
      drawScatter();
    }
    // what the screen is and how complete it is, once, at the bottom
    var copy = env.copy || {};
    [copy.coverage, copy.gaps, copy.rebased]
      .filter(function (line) {
        return typeof line === "string" && line;
      })
      .forEach(function (line) {
        body.appendChild(el("p", "sc-note", line));
      });
  }
  function rangeCount(env) {
    var r = env.range;
    if (r && typeof r.count === "number") return r.count;
    var t = env.latest && env.latest.rows ? env.latest.rows.length : null;
    return t || ((env.manifest || {}).universe || {}).total;
  }
  /** A dashboard panel: its name (a button that focuses it, when it is a
      view), then its content. */
  function panel(dash, env, kind, title) {
    var p = el(
      "section",
      "sc-panel" + (kind ? " sc-p-" + kind.toLowerCase() : " sc-p-dist"),
    );
    if (kind) p.setAttribute("data-view", kind);
    if (title) {
      var h = el(kind ? "button" : "h3", "sc-ptitle", title);
      if (kind) {
        h.type = "button";
        h.setAttribute("aria-pressed", ui.focus === kind ? "true" : "false");
        h.addEventListener("click", function () {
          ui.focus = ui.focus === kind ? null : kind;
          ui.tab = ui.focus || defaultKind(env);
          render(last);
        });
      }
      p.appendChild(h);
    }
    dash.appendChild(p);
    return p;
  }
  /** The controls up top: the view to focus (all, or one), the table's sort
      column, and Show (how many lines the Lines panel colours and names). */
  function controls(body, env, panels, hasLines) {
    var bar = el("div", "sc-controls");
    var overview = tpl(env, "overview");
    if (panels.length > 1 && overview) {
      var items = [{ value: null, label: overview }].concat(
        panels.map(function (k) {
          return { value: k, label: k };
        }),
      );
      bar.appendChild(
        group(
          null,
          seg(
            items,
            ui.focus || null,
            function (v) {
              ui.focus = v;
              ui.tab = v || defaultKind(env);
              render(last);
            },
            "sc-tabs",
          ),
        ),
      );
    }
    var plan = viewPlan(env);
    var sortWord = tpl(env, "sort");
    if (sortWord && plan.columns.length > 1) {
      var key = ui.sort ? ui.sort.key : plan.sort;
      var dir = ui.sort ? ui.sort.dir : plan.dir;
      bar.appendChild(
        group(
          sortWord,
          seg(
            plan.columns.map(function (c) {
              var lab = labelOf(env, c).label;
              return {
                value: c,
                label: c === key ? lab + (dir < 0 ? " ↓" : " ↑") : lab,
              };
            }),
            key,
            function (c) {
              ui.sort = { key: c, dir: c === key ? -dir : -1 };
              render(last);
            },
          ),
        ),
      );
    }
    var showWord = tpl(env, "show");
    var total = hasLines ? (env.hero.assets || []).length : 0;
    var sizes = [TOP, 10].filter(function (s) {
      return s < total;
    });
    if (hasLines && showWord && sizes.length) {
      var opts = sizes.map(function (s) {
        return { value: s, label: String(s) };
      });
      opts.push({
        value: total,
        label: tpl(env, "show_all", { n: total }) || String(total),
      });
      bar.appendChild(
        group(
          showWord,
          seg(
            opts,
            Math.min(ui.top != null ? ui.top : TOP, total),
            function (v) {
              ui.top = v;
              render(last);
            },
          ),
        ),
      );
    }
    if (bar.childNodes.length) body.appendChild(bar);
  }
  function group(label, control) {
    var g2 = el("div", "sc-ctl");
    if (label) g2.appendChild(el("span", "sc-lab", label));
    g2.appendChild(control);
    return g2;
  }

  /** Two `latest` panel columns as x / y, one dot per coin: the top five in
      their identity colours and named, the rest grey (§D3.5; S1
      `ssScatterCanvas`), the medians (or the author's lines) as quadrants. */
  function scatter(body, env, slot, inline) {
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
    if (panel.length < 2) return 0;
    var dc = (env.graphs || {}).default_channels || {};
    var dx = panel.indexOf(dc.x) >= 0 ? dc.x : panel[1];
    var dy = panel.indexOf(dc.y) >= 0 ? dc.y : panel[0];
    var xs = !inline && panel.indexOf(ui.sx) >= 0 ? ui.sx : dx;
    var ys = !inline && panel.indexOf(ui.sy) >= 0 ? ui.sy : dy;
    var choices = panel.map(function (nm) {
      return { value: nm, label: labelOf(env, nm).label };
    });
    (inline
      ? []
      : [
          ["Y", ys, "sy"],
          ["X", xs, "sx"],
        ]
    ).forEach(function (axis) {
      var row = el("div", "sc-ctl");
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
    // No coin has both values: no picture rather than axes over nothing.
    if (!pts.length) return 0;
    var lx = labelOf(env, xs);
    var ly = labelOf(env, ys);
    var sxS = outputSigned(env, xs);
    var syS = outputSigned(env, ys);
    var chart = el("div", "sc-chart" + (inline ? "" : " sc-tall"));
    body.appendChild(chart);
    var W = Math.max(240, (chart.clientWidth || H.cardWidth() || 600) - 2);
    var Hh = inline ? (W < 480 ? 200 : 210) : W < 480 ? 300 : 380;
    var lw = 0;
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
    if (typeof dc.quadrant_x === "number") {
      xr[0] = Math.min(xr[0], dc.quadrant_x);
      xr[1] = Math.max(xr[1], dc.quadrant_x);
    }
    if (typeof dc.quadrant_y === "number") {
      yr[0] = Math.min(yr[0], dc.quadrant_y);
      yr[1] = Math.max(yr[1], dc.quadrant_y);
    }
    [0, 0.5, 1].forEach(function (q) {
      lw = Math.max(
        lw,
        textW(fmtValue(env, yr[0] + q * (yr[1] - yr[0]), ly.unit, syS, true)),
      );
    });
    var P = { l: 26 + lw, r: 12, t: 12, b: 38 };
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
      "aria-label": ly.label + SEP + lx.label,
    });
    var num = function (v) {
      return typeof v === "number" && isFinite(v) ? v : null;
    };
    var mx = num(dc.quadrant_x);
    var my = num(dc.quadrant_y);
    if (mx === null) mx = med("x");
    if (my === null) my = med("y");
    g(
      "line",
      { class: "sc-median", x1: X(mx), x2: X(mx), y1: P.t, y2: Hh - P.b },
      svg,
    );
    g(
      "line",
      { class: "sc-median", x1: P.l, x2: W - P.r, y1: Y(my), y2: Y(my) },
      svg,
    );
    var quads = [];
    var qw = cardCopy(env).quadrants;
    if (qw && !Array.isArray(qw) && typeof qw === "object")
      qw = [qw.top_left, qw.top_right, qw.bottom_left, qw.bottom_right];
    if (Array.isArray(qw) && qw.length === 4)
      [
        [P.l + 6, P.t + 10, "start"],
        [W - P.r - 6, P.t + 10, "end"],
        [P.l + 6, Hh - P.b - 8, "start"],
        [W - P.r - 6, Hh - P.b - 8, "end"],
      ].forEach(function (q, i) {
        if (typeof qw[i] !== "string" || !qw[i]) return;
        quads.push({ x: q[0], y: q[1], anchor: q[2], text: qw[i] });
      });
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
    ax.textContent = headerLabel(env, xs);
    svg.appendChild(ax);
    var ay = svgEl("text", {
      class: "sc-axis",
      x: 10,
      y: midY,
      "text-anchor": "middle",
      transform: "rotate(-90 10 " + midY + ")",
    });
    ay.textContent = headerLabel(env, ys);
    svg.appendChild(ay);
    var named = [];
    pts
      .slice()
      .sort(function (p, q) {
        var a = slot[p.a] != null ? IDS - slot[p.a] : 0;
        var b2 = slot[q.a] != null ? IDS - slot[q.a] : 0;
        return a - b2;
      })
      .forEach(function (p) {
        var r = slot[p.a];
        var on = r != null && r < TOP;
        var c = svgEl("circle", {
          class:
            "sc-dot" +
            (on ? "" : " sc-out") +
            (ui.iso && ui.iso !== p.a ? " sc-dim" : ""),
          cx: X(p.x),
          cy: Y(p.y),
          r: on ? (r === 0 ? 6 : 5) : 3,
          fill: on ? idVar(r) : null,
          "data-asset": p.a,
        });
        var title = svgEl("title", {});
        title.textContent =
          p.a +
          SEP +
          lx.label +
          " " +
          fmtValue(env, p.x, lx.unit, sxS) +
          SEP +
          ly.label +
          " " +
          fmtValue(env, p.y, ly.unit, syS);
        c.appendChild(title);
        svg.appendChild(c);
        if (ui.iso === p.a || on)
          named.push({ a: p.a, x: X(p.x) + 9, y: Y(p.y), r: r, on: on });
      });
    // the names: nudged apart where two would touch, never over a quadrant word
    named.sort(function (p, q) {
      return p.y - q.y;
    });
    for (var ni = 1; ni < named.length; ni++)
      for (var nj = 0; nj < ni; nj++)
        if (
          Math.abs(named[ni].y - named[nj].y) < 13 &&
          Math.abs(named[ni].x - named[nj].x) < textW(named[nj].a) + 6
        )
          named[ni].y = named[nj].y + 13;
    named.forEach(function (n) {
      var right = n.x + textW(n.a) <= W - P.r;
      var lab = svgEl("text", {
        class: "sc-dotlab" + (n.r === 0 ? " sc-first" : ""),
        x: right ? n.x : n.x - 18,
        y: n.y,
        "text-anchor": right ? "start" : "end",
        fill: n.on ? idVar(n.r) : null,
      });
      lab.textContent = n.a;
      svg.appendChild(lab);
    });
    quads.forEach(function (q) {
      var w = textW(q.text);
      var x0 = q.anchor === "start" ? q.x : q.x - w;
      var clash = named.some(function (n) {
        var nx0 = n.x - (n.x + textW(n.a) <= W - P.r ? 0 : textW(n.a) + 18);
        return (
          Math.abs(n.y - q.y) < 14 &&
          nx0 < x0 + w + 4 &&
          nx0 + textW(n.a) > x0 - 4
        );
      });
      if (clash) return;
      var tq = svgEl("text", {
        class: "sc-quad",
        x: q.x,
        y: q.y,
        "text-anchor": q.anchor,
      });
      tq.textContent = q.text;
      svg.appendChild(tq);
    });
    chart.appendChild(svg);
    chart.addEventListener("click", function (event) {
      var a =
        event.target &&
        event.target.getAttribute &&
        event.target.getAttribute("data-asset");
      if (a) isolate(a);
    });
    return Math.min(TOP, pts.length);
  }

  /** The fullscreen table (§D3.5; Q-3635, Q-7926): rank, coin, every
      declared column, every row of the set; the sort column carries a data
      bar (identity colour for the top six, the ink's quiet grey after);
      tied ranks share their number ("11=") with the tie named under it;
      headers sort (↓/↑). Rules are `--rule`, never the host's 40 % border. */
  function table(body, env, slot) {
    var plan = viewPlan(env);
    var set = setRows(env, plan);
    if (!set.rows.length) return;
    var whole = plan.table;
    var cols = plan.columns.slice();
    var key = plan.sort;
    var dir = plan.dir;
    if (
      ui.sort &&
      (ui.sort.key === "" || whole.columns.indexOf(ui.sort.key) >= 0)
    ) {
      key = ui.sort.key;
      dir = ui.sort.dir;
    }
    var rows = set.rows;
    if (key !== plan.sort || dir !== plan.dir) {
      var ki0 = key === "" ? 0 : whole.columns.indexOf(key);
      rows = sortRows(rows, ki0, dir);
    }
    var wrap = el("div", "sc-tw");
    var tbl = el("table", "sc-dt");
    var thead = el("thead");
    var hr = el("tr");
    var headers = [
      { c: null, text: "#", cls: "sc-rkc" },
      { c: "", text: "", cls: "sc-t" },
    ].concat(
      cols.map(function (c) {
        return { c: c, text: headerLabel(env, c), cls: "sc-n" };
      }),
    );
    headers.forEach(function (h) {
      var sorted = h.c != null && h.c === key;
      var text = h.text + (sorted ? (dir < 0 ? " ↓" : " ↑") : "");
      var th = el(
        "th",
        h.cls + (sorted ? " sc-s" : ""),
        h.c == null ? text : "",
      );
      th.setAttribute("scope", "col");
      if (h.c != null) {
        th.setAttribute(
          "aria-sort",
          sorted ? (dir < 0 ? "descending" : "ascending") : "none",
        );
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
    var kj = whole.columns.indexOf(key);
    var signed = {};
    cols.forEach(function (c) {
      signed[c] = outputSigned(env, c);
    });
    var diverge =
      kj > 0 &&
      rows.some(function (r) {
        return typeof r[kj] === "number" && r[kj] < 0;
      });
    var ext = 1;
    if (kj > 0)
      ext =
        Math.max.apply(
          null,
          rows.map(function (r) {
            return typeof r[kj] === "number" ? Math.abs(r[kj]) : 0;
          }),
        ) || 1;
    var tie = set.filter.tie;
    var si = whole.columns.indexOf(plan.sort);
    var tieRank = null;
    var entered = {};
    ((env.passing || {}).entered || []).forEach(function (a) {
      entered[String(a)] = true;
    });
    var tb = el("tbody");
    rows.forEach(function (r, i) {
      var asset = String(r[0]);
      var tr = el("tr", ui.iso === asset ? "sc-sel" : "");
      tr.setAttribute("data-asset", asset);
      tr.addEventListener("click", function () {
        isolate(asset);
      });
      var isTie =
        tie &&
        tie.count > 1 &&
        typeof tie.value === "number" &&
        key === plan.sort &&
        r[si] === tie.value;
      if (isTie && tieRank == null) tieRank = i + 1;
      tr.appendChild(
        el("td", "sc-rkc", isTie ? String(tieRank) + "=" : String(i + 1)),
      );
      var td0 = el("td", "sc-t");
      td0.appendChild(document.createTextNode(asset));
      if (entered[asset]) {
        var nw = tpl(env, "new");
        if (nw) td0.appendChild(el("span", "sc-new", nw));
      }
      tr.appendChild(td0);
      cols.forEach(function (c) {
        var j = whole.columns.indexOf(c);
        var v = r[j];
        var td = el("td", "sc-n" + (c === key ? " sc-s" : ""));
        var text = fmtValue(env, v, labelOf(env, c).unit, signed[c], true);
        if (c === key && kj > 0) {
          var cell = el("span", "sc-cell");
          var bar = el("span", "sc-bar" + (diverge ? " sc-div" : ""));
          var fill = el("i", "");
          var share = typeof v === "number" ? Math.abs(v) / ext : 0;
          if (diverge) {
            fill.style.width = (share * 50).toFixed(1) + "%";
            fill.style.left = (v < 0 ? 50 - share * 50 : 50).toFixed(1) + "%";
          } else fill.style.width = (share * 100).toFixed(1) + "%";
          var cv = idVar(slot[asset]);
          if (cv) fill.style.background = cv;
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
    if (tie && tie.count > 1 && typeof tie.value === "number") {
      var lab = labelOf(env, plan.sort);
      var words = tpl(env, "tie", {
        n: tie.count,
        value: fmtValue(env, tie.value, lab.unit, outputSigned(env, plan.sort)),
      });
      if (words) body.appendChild(el("p", "sc-note sc-tie", words));
    }
    if (set.size > rows.length) {
      var more = tpl(env, "more_in_keel", { k: set.size - rows.length });
      if (more) body.appendChild(el("p", "sc-note sc-more", more));
    }
  }

  H.ready(function (env) {
    last = env || {};
    render(last);
  });
  H.onResize(function () {
    if (last) render(last);
  });
  if (typeof H.onTheme === "function")
    H.onTheme(function () {
      if (last) render(last);
    });
})();
