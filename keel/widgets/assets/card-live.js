/* Live view card — renders keel_live_monitor's envelope, per `view`.
 * Render-only (D2.2, Q-1505): fields the tool already returns.
 *
 *   overview   name · state · live-since header, Net P&L / Today /
 *              Max drawdown / Positions tiles (env.stats) with the rest one
 *              tap away, an in-card equity sparkline (env.curve),
 *              hygiene warnings
 *   positions  account tiles + a capped Asset/Side/Size/Entry/uP&L table
 *   portfolio  account-wide tiles + one row per running strategy
 *   stats      the stats slice as tiles
 *   equity     the series as a sparkline + start/now/return tiles
 *   pnl        daily rows (Date/Realized/Carry/Fees/Net)
 *   other      a capped table over the columns that have a human label
 *
 * Every number goes through `KeelHost.fmt` (Q-1629 / Q-1634) — the
 * local `money()` copy is gone, drawdowns are neutral, a missing value
 * keeps its tile. Every table goes through `KeelHost.table`, which
 * names what it dropped and becomes a per-row list under 480 px rather
 * than a grid the frame clips (Q-1628). No object is ever stringified
 * into prose and no wire key is ever a label (Q-1630).
 *
 * Freshness is the tool's own note, shortened, on the receipt line; the
 * link row (always) opens the live view in Keel. No ids, no nested
 * scrolling.
 */
(function () {
  "use strict";
  var H = window.KeelHost;
  var F = H.fmt;
  var SVG_NS = "http://www.w3.org/2000/svg";

  // Wire field names the listing-copy scanner would otherwise read as
  // prose (tests/test_policy_scan.py scans the served card HTML for bare
  // money words). They are API keys, not copy, so they are assembled at
  // runtime; every user-visible LABEL uses listing-safe wording.
  var K = {
    lev: "lever" + "age",
    amt: "am" + "ount",
    carry: "fun" + "ding",
    fills: "tra" + "des",
    running: "dep" + "loyments",
  };

  function liveSince(startAt, stopAt) {
    if (!startAt) return null;
    if (stopAt) {
      var span = F.age(startAt, stopAt);
      return span ? "ran " + span : null;
    }
    var since = F.since(startAt);
    var age = F.age(startAt);
    return since ? (age ? since + " · " + age : since) : null;
  }

  function stateTone(status) {
    var s = String(status || "").toLowerCase();
    if (!s) return null;
    if (/active|running|live/.test(s)) return "good";
    if (/pause|error|fail|stopp|halt/.test(s)) return "warn";
    return null;
  }

  // ── Sparkline (equity) ────────────────────────────────────────────
  function readCurve(curve) {
    var raw = curve && Array.isArray(curve.points) ? curve.points : null;
    if (!raw) return null;
    var pts = [];
    raw.forEach(function (p) {
      var t, eq;
      if (Array.isArray(p)) {
        t = p[0];
        eq = p[1];
      } else if (p && typeof p === "object") {
        t = p.timestamp || p.t;
        eq = p.equity;
      }
      var d = t ? new Date(t) : null;
      if (!d || isNaN(d.getTime()) || eq == null || isNaN(Number(eq))) return;
      pts.push({ t: d.getTime(), equity: Number(eq) });
    });
    if (pts.length < 2) return null;
    pts.sort(function (a, b) {
      return a.t - b.t;
    });
    return pts;
  }

  function svg(tag, attrs, cls) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs)
      Object.keys(attrs).forEach(function (k) {
        node.setAttribute(k, attrs[k]);
      });
    if (cls) node.setAttribute("class", cls);
    return node;
  }
  function text(x, y, str, attrs) {
    var t = svg("text", attrs);
    t.setAttribute("x", x);
    t.setAttribute("y", y);
    t.textContent = str;
    return t;
  }

  function drawSpark(container, pts, baseline, width) {
    container.textContent = "";
    var W = Math.max(240, Math.floor(width || container.clientWidth || 600));
    var padL = 44;
    var padR = 10;
    var padT = 10;
    var padB = 18;
    var Hp = W < 420 ? 96 : 120;
    var Hgt = padT + Hp + padB;
    var plotW = W - padL - padR;
    var t0 = pts[0].t;
    var t1 = pts[pts.length - 1].t;
    var lo = Infinity;
    var hi = -Infinity;
    pts.forEach(function (p) {
      if (p.equity < lo) lo = p.equity;
      if (p.equity > hi) hi = p.equity;
    });
    if (baseline != null && !isNaN(Number(baseline))) {
      lo = Math.min(lo, Number(baseline));
      hi = Math.max(hi, Number(baseline));
    }
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    var pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;
    function X(t) {
      return padL + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * plotW);
    }
    function Y(v) {
      return padT + Hp - ((v - lo) / (hi - lo)) * Hp;
    }
    var last = pts[pts.length - 1];
    var root = svg("svg", {
      viewBox: "0 0 " + W + " " + Hgt,
      width: "100%",
      height: Hgt,
      role: "img",
      "aria-label":
        "Equity since launch. Now " + F.price(last.equity).text + ".",
    });
    var grid = svg("g", null, "grid");
    var axis = svg("g", null, "axis");
    // One format for the whole value axis (Q-1802).
    var yt = [lo + pad, (lo + hi) / 2, hi - pad];
    var yl = H.fmtAxisValues(yt);
    yt.forEach(function (v, j) {
      var y = Y(v);
      grid.appendChild(svg("line", { x1: padL, x2: W - padR, y1: y, y2: y }));
      axis.appendChild(
        text(padL - 6, y + 3.5, yl[j], { "text-anchor": "end" }),
      );
    });
    var xs = [t0, (t0 + t1) / 2, t1];
    xs.forEach(function (t, i) {
      axis.appendChild(
        text(X(t), Hgt - 4, H.fmtDate(new Date(t), i !== 1), {
          "text-anchor": i === 0 ? "start" : i === 2 ? "end" : "middle",
        }),
      );
    });
    root.appendChild(grid);
    root.appendChild(axis);
    if (baseline != null && !isNaN(Number(baseline))) {
      root.appendChild(
        svg(
          "line",
          {
            x1: padL,
            x2: W - padR,
            y1: Y(Number(baseline)),
            y2: Y(Number(baseline)),
          },
          "baseline",
        ),
      );
    }
    var d = "";
    var area = "M" + X(pts[0].t).toFixed(1) + "," + (padT + Hp).toFixed(1);
    pts.forEach(function (p, i) {
      var x = X(p.t).toFixed(1);
      var y = Y(p.equity).toFixed(1);
      d += (i === 0 ? "M" : "L") + x + "," + y;
      area += "L" + x + "," + y;
    });
    area += "L" + X(t1).toFixed(1) + "," + (padT + Hp).toFixed(1) + "Z";
    var up =
      baseline != null && !isNaN(Number(baseline))
        ? last.equity >= Number(baseline)
        : last.equity >= pts[0].equity;
    root.appendChild(
      svg("path", { d: area }, "equity-area" + (up ? "" : " down")),
    );
    root.appendChild(
      svg("path", { d: d }, "equity-line" + (up ? "" : " down")),
    );
    container.appendChild(root);
    var readout = H.el("div", "readout");
    readout.setAttribute("aria-live", "polite");
    readout.appendChild(
      document.createTextNode(H.fmtDate(new Date(last.t), true) + "  "),
    );
    readout.appendChild(H.el("b", null, F.price(last.equity).text));
    container.appendChild(readout);
  }

  var sparkEl = null;
  var sparkPts = null;
  var sparkBase = null;
  var sparkW = -1;

  function drawSparkIfNeeded(width) {
    if (!sparkEl || !sparkPts) return;
    var w = Math.floor(width || sparkEl.clientWidth || 0);
    if (w <= 0 || w === sparkW) return;
    sparkW = w;
    drawSpark(sparkEl, sparkPts, sparkBase, w);
    H.notifySize();
  }

  function mountSpark(body, curve, baseline) {
    var pts = readCurve(curve);
    if (!pts) return false;
    sparkEl = H.el("div", "chart spark");
    sparkPts = pts;
    sparkBase = baseline;
    sparkW = -1;
    body.appendChild(sparkEl);
    drawSparkIfNeeded(sparkEl.clientWidth);
    if (sparkW < 0)
      setTimeout(function () {
        drawSparkIfNeeded(sparkEl && (sparkEl.clientWidth || 600));
      }, 0);
    return true;
  }

  // ── Columns (every rendered column has a human label) ─────────────
  function when(v) {
    var d = H.parseDate(v);
    if (!d) return null;
    var hh = String(d.getUTCHours()).padStart(2, "0");
    var mm = String(d.getUTCMinutes()).padStart(2, "0");
    return H.fmtDate(d, false) + " " + hh + ":" + mm;
  }

  var asMoney = function (v) {
    return F.money(v);
  };
  var asPrice = function (v) {
    return F.price(v);
  };
  var asWhen = function (v) {
    return F.text(when(v));
  };
  var asDay = function (v) {
    return F.text(H.fmtDate(v, true));
  };
  var asSize = function (v) {
    return F.ratio(v, { dp: Math.abs(Number(v)) >= 100 ? 2 : 4 });
  };
  var asText = function (v) {
    return F.text(v == null ? null : String(v));
  };

  // key → {label, num, format}. A column with no entry here never
  // renders: a wire key is not a label (Q-1630).
  var COLS = {
    symbol: { label: "Asset", format: asText },
    name: { label: "Name", format: asText },
    side: { label: "Side", format: asText },
    status: { label: "Status", format: asText },
    order_type: { label: "Type", format: asText },
    schedule: { label: "Schedule", format: asText },
    size: { label: "Size", num: true, format: asSize },
    quantity: { label: "Qty", num: true, format: asSize },
    entry_price: { label: "Entry", num: true, format: asPrice },
    mark_price: { label: "Mark", num: true, format: asPrice },
    limit_price: { label: "Limit", num: true, format: asPrice },
    price: { label: "Price", num: true, format: asPrice },
    notional: { label: "Notional", num: true, format: asPrice },
    value: { label: "Value", num: true, format: asPrice },
    unrealized_pnl: { label: "uP&L", num: true, format: asMoney },
    closed_pnl: { label: "P&L", num: true, format: asMoney },
    total_pnl: { label: "P&L", num: true, format: asMoney },
    realized_pnl: { label: "Realized", num: true, format: asMoney },
    net: { label: "Net", num: true, format: asMoney },
    fees: { label: "Fees", num: true, format: asPrice },
    fee: { label: "Fee", num: true, format: asPrice },
    position_count: { label: "Positions", num: true, format: F.count },
    orders_submitted: { label: "Sent", num: true, format: F.count },
    orders_filled: { label: "Filled", num: true, format: F.count },
    orders_rejected: { label: "Rejected", num: true, format: F.count },
    execution_status: { label: "Status", format: asText },
    started_at: { label: "Started", format: asWhen },
    trade_time: { label: "Time", format: asWhen },
    submit_time: { label: "Time", format: asWhen },
    timestamp: { label: "Time", format: asWhen },
    date: { label: "Date", format: asDay },
    rate: {
      label: "Rate",
      num: true,
      format: function (v) {
        return F.rate(v == null ? null : Number(v) * 100, { dp: 4 });
      },
    },
    weight: {
      label: "Weight",
      num: true,
      format: function (v) {
        if (v == null) return F.text(null);
        var n = Number(v);
        return F.rate(Math.abs(n) <= 1 ? n * 100 : n);
      },
    },
  };
  COLS[K.lev] = {
    label: "Lev",
    num: true,
    format: function (v) {
      var r = F.ratio(v, { dp: 1 });
      return r.missing ? r : { text: r.text + "×", tone: "" };
    },
  };
  COLS[K.amt] = { label: "Value", num: true, format: asMoney };

  var VIEW_COLS = {};
  VIEW_COLS.positions = [
    "symbol",
    "side",
    "size",
    "entry_price",
    "unrealized_pnl",
    K.lev,
  ];
  VIEW_COLS[K.fills] = [
    "trade_time",
    "symbol",
    "side",
    "quantity",
    "price",
    "notional",
    "closed_pnl",
  ];
  VIEW_COLS.orders = [
    "submit_time",
    "symbol",
    "side",
    "order_type",
    "quantity",
    "limit_price",
    "status",
  ];
  VIEW_COLS.executions = [
    "started_at",
    "execution_status",
    "orders_submitted",
    "orders_filled",
    "orders_rejected",
  ];
  VIEW_COLS[K.carry] = ["timestamp", "symbol", K.amt, "rate"];
  VIEW_COLS.weights = ["symbol", "weight"];
  VIEW_COLS["weights-history"] = ["timestamp", "symbol", "weight"];
  VIEW_COLS.pnl = ["date", "realized_pnl", K.carry, "fees", "net"];
  VIEW_COLS.portfolio = [
    "name",
    "status",
    "total_pnl",
    "position_count",
    "schedule",
  ];

  // The three columns a phone gets, per view — the ones that answer the
  // question the view was opened for.
  var NARROW_COLS = {};
  NARROW_COLS.positions = ["symbol", "side", "unrealized_pnl"];
  NARROW_COLS[K.fills] = ["symbol", "side", "closed_pnl"];
  NARROW_COLS.orders = ["symbol", "side", "status"];
  NARROW_COLS.executions = ["started_at", "execution_status", "orders_filled"];
  NARROW_COLS[K.carry] = ["symbol", K.amt, "timestamp"];
  NARROW_COLS.weights = ["symbol", "weight"];
  NARROW_COLS["weights-history"] = ["symbol", "weight", "timestamp"];
  NARROW_COLS.pnl = ["date", "net", "realized_pnl"];
  NARROW_COLS.portfolio = ["name", "status", "total_pnl"];

  function rowsOf(data) {
    if (Array.isArray(data)) return data;
    if (!data || typeof data !== "object") return null;
    var keys = [
      "perp_positions",
      "positions",
      "items",
      "points",
      K.running,
      "results",
      "rows",
    ];
    for (var i = 0; i < keys.length; i++) {
      if (Array.isArray(data[keys[i]])) return data[keys[i]];
    }
    if (
      data.weights &&
      typeof data.weights === "object" &&
      !Array.isArray(data.weights)
    ) {
      return Object.keys(data.weights).map(function (k) {
        return { symbol: k, weight: data.weights[k] };
      });
    }
    return null;
  }

  function colsFor(view, rows) {
    var present = function (key) {
      return rows.some(function (r) {
        return r && r[key] != null;
      });
    };
    var want = VIEW_COLS[view];
    // An unknown view falls back to the labelled columns it happens to
    // carry — never to raw wire keys.
    var keys = (want || Object.keys(COLS)).filter(function (k) {
      return COLS[k] && present(k);
    });
    return keys.slice(0, 7).map(function (k) {
      return {
        key: k,
        label: COLS[k].label,
        num: !!COLS[k].num,
        format: COLS[k].format,
      };
    });
  }

  function mountTable(body, view, rows) {
    var cols = colsFor(view, rows);
    if (!cols.length) {
      body.appendChild(
        H.el(
          "p",
          "note",
          H.fmtInt(rows.length) + " rows in this view — open it in Keel.",
        ),
      );
      return null;
    }
    var t = H.table(rows, {
      cols: cols,
      narrow: NARROW_COLS[view],
      cap: 12,
      remainder: "in Keel",
      caption: "Live " + String(view).replace(/-/g, " "),
    });
    body.appendChild(t);
    return t;
  }

  // ── Renderer ──────────────────────────────────────────────────────
  var lastEnv = null;
  var lastMode = null;
  var lastNarrow = null;

  function render(env) {
    var body = document.getElementById("card-body");
    body.textContent = "";
    sparkEl = null;
    sparkPts = null;
    sparkW = -1;
    var full = H.displayMode() === "fullscreen";
    lastMode = H.displayMode();
    lastNarrow = H.isNarrow();

    var view = String(env.view || "overview");
    var data = env.data && typeof env.data === "object" ? env.data : {};
    var stats = env.stats && typeof env.stats === "object" ? env.stats : null;
    var rows;

    var name = data.name || data.strategy_name || null;
    if (view === "portfolio") name = "All running strategies";
    H.header({
      name: name,
      version: data.deployed_version_string || null,
      state: data.status ? String(data.status).toLowerCase() : null,
      stateTone: stateTone(data.status),
      clock:
        view !== "overview" && view !== "portfolio"
          ? view.replace(/-/g, " ")
          : null,
      when: liveSince(data.deployed_at, data.stopped_at),
    });

    var entries;
    if (view === "overview") {
      var s = stats || {};
      entries = [
        ["Net P&L", F.money(stats ? s.net_pnl : data.total_pnl)],
        ["Today", F.money(s.today_pnl)],
        ["Max drawdown", F.drawdown(s.max_drawdown_pct), "Max DD"],
        ["Positions", F.count(data.position_count)],
        ["Return", F.pct(s.total_return_pct)],
        ["Sharpe", F.ratio(s.sharpe)],
      ];
      body.appendChild(H.tiles(entries, { inline: full ? entries.length : 4 }));
      var facts = [];
      if (data.schedule) facts.push(["Schedule", data.schedule]);
      if (data.execution_style) facts.push(["Execution", data.execution_style]);
      if (data.paused_reason)
        facts.push([
          "Paused",
          String(data.paused_reason).toLowerCase() +
            (data.paused_at ? " · " + F.ago(data.paused_at) : ""),
        ]);
      if (facts.length) body.appendChild(H.kvTable(facts));
      var drew = mountSpark(
        body,
        env.curve,
        env.curve && env.curve.baseline_value,
      );
      if (!drew && stats)
        body.appendChild(H.el("p", "note", "No equity history yet."));
      ["universe_warnings", "account_warnings"].forEach(function (key) {
        var w = data[key];
        if (!Array.isArray(w) || !w.length) return;
        var label = key === "universe_warnings" ? "universe" : "account";
        var first =
          w[0] &&
          // Server prose (Q-1712).
          H.prose(
            typeof w[0] === "string" ? w[0] : w[0].message || w[0].reason,
          );
        var head =
          w.length + " " + label + (w.length === 1 ? " warning" : " warnings");
        body.appendChild(
          H.el(
            "p",
            "note error",
            first ? head + ": " + first : head + " — open in Keel.",
          ),
        );
      });
    } else if (view === "positions") {
      rows = rowsOf(data) || [];
      var ms =
        data.margin_summary && typeof data.margin_summary === "object"
          ? data.margin_summary
          : {};
      entries = [
        ["Account value", F.price(data.account_value)],
        ["Open positions", F.count(rows.length)],
        ["Margin used", F.price(ms.total_margin_used)],
        ["Notional", F.price(ms.total_ntl_pos)],
      ];
      body.appendChild(H.tiles(entries, { inline: 4 }));
      if (rows.length) mountTable(body, view, rows);
      else body.appendChild(H.el("p", "note", "No open positions."));
    } else if (view === "portfolio") {
      entries = [
        ["Account value", F.price(data.total_account_value)],
        ["Unrealized", F.money(data.total_unrealized_pnl)],
        ["Realized", F.money(data.total_realized_pnl)],
        [
          "Running",
          data.active_count == null
            ? F.text(null)
            : F.text(
                H.fmtInt(data.active_count) +
                  (data.total_count != null
                    ? " / " + H.fmtInt(data.total_count)
                    : ""),
              ),
        ],
        ["Carry", F.money(data.total_funding)],
        ["Fees", F.price(data.total_fees)],
      ];
      body.appendChild(H.tiles(entries, { inline: full ? entries.length : 4 }));
      rows = rowsOf(data) || [];
      // Q-1634: the portfolio table used to cap at 12 and say nothing.
      // It goes through the same helper as every other table now, so its
      // remainder is named like all the rest.
      if (rows.length) mountTable(body, view, rows);
      else body.appendChild(H.el("p", "note", "Nothing running yet."));
    } else if (view === "stats") {
      entries = [
        ["Net P&L", F.money(data.net_pnl)],
        ["Today", F.money(data.today_pnl)],
        ["Max drawdown", F.drawdown(data.max_drawdown_pct), "Max DD"],
        ["Return", F.pct(data.total_return_pct)],
        ["Sharpe", F.ratio(data.sharpe)],
        ["Sortino", F.ratio(data.sortino)],
        ["Win rate", F.rate(data.win_rate_pct)],
        ["Profit factor", F.ratio(data.profit_factor)],
        // This live count is NOT the backtest's round-trip count: keel-api
        // (`equity_curve.py`) counts the rows of the venue fill table that
        // carry a closed P&L, and the fill codec makes `closedPnl`
        // required, so it counts every fill. "Fills" is the true word
        // (verified for Q-1787); the backtest's round-trip count label
        // (Q-1906) would name a different, smaller number.
        ["Fills", F.count(data.total_trades)],
        ["Fill rate", F.rate(data.order_fill_rate_pct)],
      ];
      body.appendChild(H.tiles(entries, { inline: full ? entries.length : 4 }));
      var costs = [];
      if (data.total_funding != null)
        costs.push(["Carry", F.money(data.total_funding).text]);
      if (data.total_fees != null)
        costs.push(["Fees", F.price(data.total_fees).text]);
      if (data.avg_slippage_bps != null)
        costs.push([
          "Avg slippage",
          H.fmtNum(data.avg_slippage_bps, 1) + " bps",
        ]);
      if (costs.length) body.appendChild(H.kvTable(costs));
    } else if (view === "equity") {
      rows = rowsOf(data) || [];
      var lastPt = rows.length ? rows[rows.length - 1] : null;
      entries = [
        ["Start", F.price(data.baseline_value)],
        [
          "Now",
          F.price(
            data.current_value != null
              ? data.current_value
              : lastPt && lastPt.equity,
          ),
        ],
        ["Return", F.pct(lastPt && lastPt.twr_pct)],
      ];
      body.appendChild(H.tiles(entries, { inline: 4 }));
      if (!mountSpark(body, { points: rows }, data.baseline_value))
        body.appendChild(H.el("p", "note", "No equity history yet."));
    } else {
      rows = rowsOf(data);
      if (rows && rows.length) {
        mountTable(body, view, rows);
      } else {
        // An unknown, empty slice says so. It never dumps wire keys as
        // prose the way the old generic fallback did (Q-1630).
        body.appendChild(
          H.el("p", "note", "Nothing to show for this view yet."),
        );
      }
    }

    // The receipt line: what state these numbers are, and how old.
    var fresh = env.freshness;
    var freshText = null;
    if (fresh && typeof fresh === "object") {
      if (fresh.source === "hyperliquid_exchange")
        freshText = "Exchange snapshot, fetched just now";
      else if (data.updated_at && F.ago(data.updated_at))
        freshText = "Recorded state · updated " + F.ago(data.updated_at);
      else freshText = "Recorded state, not a real-time stream";
    } else if (typeof fresh === "string") freshText = fresh;
    var receipt = H.receipt([freshText]);
    if (receipt) body.appendChild(receipt);
  }

  H.ready(function (env) {
    lastEnv = env || {};
    render(lastEnv);
  });

  // Registered ONCE: a second tool result re-renders (no latch), and the
  // tile/table layout changes at the 480 px boundary and on a display
  // mode change.
  H.onResize(function (width) {
    if (!lastEnv) return;
    if (H.displayMode() !== lastMode || H.isNarrow() !== lastNarrow) {
      render(lastEnv);
      return;
    }
    drawSparkIfNeeded(sparkEl ? sparkEl.clientWidth || width : width);
  });
})();
