/* Backtest result card — renders keel_backtest_summarize's envelope.
 *
 * Render-only (D2.2): the one header shape (Keel · name · version ·
 * state · window), four tiles with the rest one tap away, the receipt
 * line that says what the numbers belong to, the equity/drawdown chart
 * drawn IN-CARD as inline SVG from the envelope's `curve` (Q-1505 — no
 * nested iframe, no fetch), and the plain link row (always).
 *
 * Every number goes through `KeelHost.fmt` (Q-1629 / Q-1634): a sign
 * glyph always accompanies colour, drawdowns and ratios are neutral,
 * a missing value keeps its tile as an em dash. A missing name is
 * "Untitled", never an id (Q-1630).
 *
 * Two sizes (BUILD §4.1, Q-1688). `view.size === "receipt"` — what
 * `keel_backtest_run` and `keel_backtest_watch` attach — draws ONE row
 * through the adapter's `H.receiptRow`, with the evidence layout one
 * tap away IN PLACE; `evidence` (summarize, or `present`) draws the
 * layout directly. An envelope with no `view` at all is an older
 * summarize result and renders as evidence, which is what it always
 * did. `view.metrics` is the one owner of metric key names and signs
 * when a view is present (§2.1); `summary_metrics` is the fallback for
 * those older envelopes and for nothing else.
 *
 * Adding run/watch to CARD_TOOLS means a quota refusal, an invalid
 * config or a NotFound now MOUNTS this card, because those are
 * returned as JSON error envelopes rather than raised. Such an
 * envelope has `code` + `message` and no `view`, and renders as one
 * honest error line — never "Untitled" over four em dashes.
 */
(function () {
  "use strict";
  var H = window.KeelHost;
  var F = H.fmt;
  var SVG_NS = "http://www.w3.org/2000/svg";

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

  // Normalise the envelope's curve: [[t, equity, dd], ...] triples (the
  // SDK's compact form) or {t, equity, drawdown_pct} objects.
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
        label: t,
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

  /**
   * The day each point's readout names (Q-1883). The curve's last mark is
   * stamped at the window's EXCLUSIVE end — the close of the last covered
   * day — so it read "Sep 23" under a range that ends "Sep 22". A point
   * past the last covered day is labelled by that day, the same rule the
   * range line applies (`H.lastCoveredDay`, spec 03 §2.5); positions on
   * the chart are untouched.
   */
  function coverDays(pts, period) {
    var last = H.parseDate(
      H.lastCoveredDay({ end: period.end_date, last_bar: period.last_bar }),
    );
    if (!last) return;
    var cap = last.getTime();
    var nextDay = cap + 86400000;
    pts.forEach(function (p) {
      p.day = p.t >= nextDay ? cap : p.t;
    });
  }

  // "Nice" tick values for an axis: 3–4 round steps across [lo, hi].
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

  function timeTicks(pts, count) {
    var t0 = pts[0].t;
    var t1 = pts[pts.length - 1].t;
    var out = [];
    for (var i = 0; i < count; i++) {
      var f = count === 1 ? 0 : i / (count - 1);
      out.push(t0 + (t1 - t0) * f);
    }
    return out;
  }

  function drawChart(container, pts, width, tall) {
    container.textContent = "";
    var W = Math.max(240, Math.floor(width || container.clientWidth || 600));
    var padL = 44;
    var padR = 10;
    var padT = 10;
    var eqH = tall ? 280 : W < 420 ? 130 : 170;
    var ddH = tall ? 96 : W < 420 ? 48 : 60;
    var gap = 14;
    var padB = 18;
    var Hgt = padT + eqH + gap + ddH + padB;
    var plotW = W - padL - padR;

    var t0 = pts[0].t;
    var t1 = pts[pts.length - 1].t;
    var eqMin = Infinity;
    var eqMax = -Infinity;
    var ddMin = 0;
    pts.forEach(function (p) {
      if (p.equity < eqMin) eqMin = p.equity;
      if (p.equity > eqMax) eqMax = p.equity;
      if (p.dd < ddMin) ddMin = p.dd;
    });
    if (eqMax === eqMin) {
      eqMax += 1;
      eqMin -= 1;
    }
    var eqPad = (eqMax - eqMin) * 0.06;
    var yLo = eqMin - eqPad;
    var yHi = eqMax + eqPad;
    if (ddMin > -0.5) ddMin = -0.5;

    function X(t) {
      return padL + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * plotW);
    }
    function Y(v) {
      return padT + eqH - ((v - yLo) / (yHi - yLo)) * eqH;
    }
    var ddTop = padT + eqH + gap;
    function YD(dd) {
      return ddTop + (dd / ddMin) * ddH;
    }

    var last = pts[pts.length - 1];
    var root = svg("svg", {
      viewBox: "0 0 " + W + " " + Hgt,
      width: "100%",
      height: Hgt,
      role: "img",
      // The label carries the VALUES, not just the chart's name — a
      // reader who cannot see the line still gets the reading (Q-1633).
      "aria-label":
        "Equity curve and drawdown. Ends at " +
        H.fmtCompact(last.equity) +
        ", deepest drawdown " +
        F.drawdown(ddMin).text +
        ".",
    });

    // Grid + y labels (equity)
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
    // Drawdown pane baseline + trough label
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
    // x labels
    var xt = timeTicks(pts, W < 420 ? 3 : 4);
    // One format for the whole axis (Q-1783).
    var xl = H.fmtAxisDates(xt);
    xt.forEach(function (t, i) {
      var anchor = i === 0 ? "start" : i === xt.length - 1 ? "end" : "middle";
      axis.appendChild(
        text(X(t), Hgt - 4, xl[i], {
          "text-anchor": anchor,
        }),
      );
    });
    root.appendChild(grid);
    root.appendChild(axis);

    // Equity area + line
    var d = "";
    var area = "M" + X(pts[0].t).toFixed(1) + "," + (padT + eqH).toFixed(1);
    pts.forEach(function (p, i) {
      var x = X(p.t).toFixed(1);
      var y = Y(p.equity).toFixed(1);
      d += (i === 0 ? "M" : "L") + x + "," + y;
      area += "L" + x + "," + y;
    });
    area +=
      "L" +
      X(pts[pts.length - 1].t).toFixed(1) +
      "," +
      (padT + eqH).toFixed(1) +
      "Z";
    root.appendChild(svg("path", { d: area }, "equity-area"));
    root.appendChild(svg("path", { d: d }, "equity-line"));

    // Drawdown area + line
    var dd = "M" + X(pts[0].t).toFixed(1) + "," + ddTop.toFixed(1);
    var ddl = "";
    pts.forEach(function (p, i) {
      var x = X(p.t).toFixed(1);
      var y = YD(p.dd).toFixed(1);
      dd += "L" + x + "," + y;
      ddl += (i === 0 ? "M" : "L") + x + "," + y;
    });
    dd +=
      "L" + X(pts[pts.length - 1].t).toFixed(1) + "," + ddTop.toFixed(1) + "Z";
    root.appendChild(svg("path", { d: dd }, "dd-area"));
    root.appendChild(svg("path", { d: ddl }, "dd-line"));

    // Crosshair + marker + readout. Reachable by pointer AND by keyboard
    // (Q-1633): the hit rect takes focus and the arrow keys walk the
    // series, while the readout is an aria-live region so the value is
    // announced rather than merely drawn.
    var cross = svg(
      "line",
      { x1: 0, x2: 0, y1: padT, y2: ddTop + ddH, visibility: "hidden" },
      "crosshair",
    );
    var marker = svg(
      "circle",
      { r: 3, cx: 0, cy: 0, visibility: "hidden" },
      "marker",
    );
    var hit = svg(
      "rect",
      {
        x: padL,
        y: padT,
        width: plotW,
        height: Hgt - padT - padB,
        tabindex: "0",
        role: "slider",
        "aria-label": "Read the curve point by point",
        "aria-valuemin": "0",
        "aria-valuemax": String(pts.length - 1),
      },
      "hit",
    );
    root.appendChild(cross);
    root.appendChild(marker);
    root.appendChild(hit);
    container.appendChild(root);

    var readout = H.el("div", "readout");
    readout.setAttribute("aria-live", "polite");
    function setReadout(p) {
      readout.textContent = "";
      readout.appendChild(
        document.createTextNode(
          H.fmtDate(new Date(p.day != null ? p.day : p.t), true) + "  ",
        ),
      );
      var b = H.el("b", null, H.fmtCompact(p.equity));
      readout.appendChild(b);
      // The percentage is the DRAWDOWN at that point, not a return — beside
      // a "+31.4%" headline an unlabelled "−0.8%" read as one (Q-1783).
      var ddv = Number(p.dd);
      readout.appendChild(
        H.el(
          "span",
          "dd",
          "  " + (ddv ? F.drawdown(ddv).text + " from peak" : "at peak"),
        ),
      );
    }
    setReadout(last);
    container.appendChild(readout);

    function at(i) {
      var p = pts[Math.max(0, Math.min(pts.length - 1, i))];
      var x = X(p.t);
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.setAttribute("visibility", "visible");
      marker.setAttribute("cx", x);
      marker.setAttribute("cy", Y(p.equity));
      marker.setAttribute("visibility", "visible");
      hit.setAttribute("aria-valuenow", String(i));
      setReadout(p);
    }
    function nearest(clientX) {
      var rect = root.getBoundingClientRect();
      var scale = rect.width ? W / rect.width : 1;
      var x = (clientX - rect.left) * scale;
      var t = t0 + ((x - padL) / plotW) * (t1 - t0);
      var lo = 0;
      var hi = pts.length - 1;
      while (lo < hi) {
        var mid = (lo + hi) >> 1;
        if (pts[mid].t < t) lo = mid + 1;
        else hi = mid;
      }
      if (lo > 0 && Math.abs(pts[lo - 1].t - t) < Math.abs(pts[lo].t - t))
        lo -= 1;
      return lo;
    }
    function show(ev) {
      cursor = nearest(ev.clientX);
      at(cursor);
    }
    function hide() {
      cross.setAttribute("visibility", "hidden");
      marker.setAttribute("visibility", "hidden");
      setReadout(last);
    }
    var cursor = pts.length - 1;
    hit.addEventListener("mousemove", show);
    hit.addEventListener("mouseleave", hide);
    hit.addEventListener(
      "touchmove",
      function (ev) {
        if (ev.touches && ev.touches[0]) show(ev.touches[0]);
      },
      { passive: true },
    );
    hit.addEventListener("touchend", hide);
    hit.addEventListener("focus", function () {
      at(cursor);
    });
    hit.addEventListener("blur", hide);
    hit.addEventListener("keydown", function (ev) {
      var step = ev.key === "PageUp" || ev.key === "PageDown" ? 10 : 1;
      if (ev.key === "ArrowLeft" || ev.key === "PageDown") cursor -= step;
      else if (ev.key === "ArrowRight" || ev.key === "PageUp") cursor += step;
      else if (ev.key === "Home") cursor = 0;
      else if (ev.key === "End") cursor = pts.length - 1;
      else return;
      ev.preventDefault();
      cursor = Math.max(0, Math.min(pts.length - 1, cursor));
      at(cursor);
    });
  }

  function pick(m, keys) {
    for (var i = 0; i < keys.length; i++) {
      if (m[keys[i]] != null) return m[keys[i]];
    }
    return null;
  }

  var STATE_TONE = { completed: "good", succeeded: "good", failed: "warn" };

  // ── The headline numbers (founder ruling 2026-09-22, Q-1781, Q-1787) ──
  // The SERVER owns which numbers are shown, in what order and in what
  // words: `view.tiles` (the app's first four — Return · Max drawdown ·
  // Sharpe · Win rate) then `view.more_tiles` (behind "+N more"), each
  // `{key, label, value}`. The card owns only FORMATTING, by key — Return
  // signed and coloured, Max drawdown neutral, a missing value "—".
  function turnoverX(v) {
    var d = F.ratio(v, { dp: 1 });
    return d.missing ? d : { text: d.text + "x", tone: "" };
  }
  function feesPaid(v) {
    return F.price(v, { dp: 0 });
  }
  var FORMAT = {
    total_return_pct: F.pct,
    max_drawdown_pct: F.drawdown,
    sharpe: F.ratio,
    win_rate_pct: F.rate,
    position_win_rate_pct: F.rate,
    round_trips: F.count, // envelopes before 2026-09-25 (served `format` since)
    sharpe_incl_warmup: F.ratio,
    turnover: turnoverX,
    sortino: F.ratio,
    calmar: F.ratio,
    profit_factor: F.ratio,
    position_profit_factor: F.ratio,
    fees_paid: feesPaid,
  };

  // ONLY for an envelope built before the server served `view.tiles`: the
  // same keys, order and words the server uses today (`HEADLINE_TILES` +
  // `MORE_TILES` in `_backtest_view.py`), with the older key spellings
  // those envelopes carried. Never a second set of labels.
  var LEGACY_TILES = [
    ["total_return_pct", "Return", ["total_return_pct", "total_return"]],
    [
      "max_drawdown_pct",
      "Max drawdown",
      ["max_drawdown_pct", "max_drawdown"],
      "Max DD",
    ],
    ["sharpe", "Sharpe", ["sharpe", "sharpe_ratio"]],
    ["win_rate_pct", "Win rate", ["win_rate_pct", "win_rate"]],
    // No count row here (Q-1906): its label is SERVED only
    // (`MORE_TILES`), because the founder's word for it is off the
    // listed static-copy word list this HTML is scanned against. A
    // pre-tiles envelope draws no count rather than an old label.
    ["turnover", "Turnover (× capital)", ["turnover"], "Turnover"],
    ["sortino", "Sortino", ["sortino", "sortino_ratio"]],
    ["calmar", "Calmar", ["calmar", "calmar_ratio"]],
    ["profit_factor", "Profit factor", ["profit_factor"]],
  ];

  /**
   * `[{key, label, value}]` in reading order: the server's `view.tiles`
   * + `view.more_tiles` verbatim when the envelope carries them, else the
   * legacy list read out of the metrics (`m`).
   */
  function tileList(v, m) {
    if (v && Array.isArray(v.tiles)) {
      var served = v.tiles.concat(
        Array.isArray(v.more_tiles) ? v.more_tiles : [],
      );
      return served.filter(function (t) {
        return t && typeof t.key === "string" && typeof t.label === "string";
      });
    }
    return LEGACY_TILES.map(function (row) {
      return {
        key: row[0],
        label: row[1],
        value: pick(m || {}, row[2]),
        short_label: row[3] || null,
      };
    });
  }

  /** One tile's display — by key; an unknown key draws the server's own. */
  function tileDisplay(t) {
    var f = (t.format && F[t.format]) || FORMAT[t.key];
    return f ? f(t.value) : F.text(t.display);
  }

  // The skeleton promises the same four tiles, labelled, before any
  // result exists (Q-1781).
  (function labelSkeleton() {
    var sk = document.querySelectorAll(".skeleton .sk-tiles i");
    for (var i = 0; i < sk.length && i < LEGACY_TILES.length; i++) {
      sk[i].textContent = LEGACY_TILES[i][1];
    }
  })();

  function viewOf(env) {
    var v = env && env.view;
    if (!v || typeof v !== "object") return null;
    // A view with no kind is an older strategy view; this card only
    // ever receives backtest-shaped ones, but be explicit.
    if (v.kind && v.kind !== "backtest") return null;
    return v;
  }

  /**
   * A refusal as `{lead, message}`, or null when this is a real result.
   *
   * TWO named shapes, no guessing between them, and they do NOT get the
   * same words (Q-1713):
   *
   * * a view that carries an `error` and no `status` — a run that was
   *   never submitted, so it has no state to be in. `Backtest not
   *   submitted — <message>` is exactly what happened (BUILD §4.1).
   *
   * * the SDK's §13.5 error envelope (`code` + `message`, no `view`).
   *   This card is declared by `keel_backtest_run` AND by
   *   `keel_backtest_summarize` / `keel_backtest_watch` at the TOOL
   *   level, so a `not_found` on a LOOKUP mounts it too — and the
   *   envelope carries no tool name for the card to tell them apart.
   *   "not submitted" is therefore a claim the card cannot support:
   *   staging's summarize-on-a-missing-id drew "Backtest not submitted"
   *   when nothing was ever being submitted. A refusal gets the
   *   refusal's own words, in the `--bad` tone the error line already
   *   carries, and no lead the card invented.
   */
  function errorOf(env) {
    if (!env) return null;
    var v = env.view;
    if (v && typeof v === "object") {
      if (v.error && !v.status) {
        var text = H.prose(v.error);
        return text
          ? { lead: "Backtest not submitted — ", message: text }
          : null;
      }
      return null;
    }
    if (
      typeof env.code === "string" &&
      typeof env.message === "string"
    ) // The server writes this sentence and it embeds the run id it
    // could not find — drawn through `prose` so it does not (Q-1712).
    // A message that was NOTHING but an id falls back to the code
    // read as words: still the envelope's own information.
    {
      var said = H.prose(env.message) || env.code.replace(/_/g, " ");
      // A server failure preamble ("Cannot backtest — …") is the server
      // saying a SUBMISSION was refused, so the card's lead is supportable
      // there and replaces it (Q-1800). A lookup's refusal carries none
      // ("Backtest … not found.") and keeps its own words, no lead (Q-1713).
      return {
        lead:
          H.withoutFailureLead(said) !== said
            ? "Backtest not submitted — "
            : null,
        message: said,
      };
    }
    return null;
  }

  // "Aug 15, 2024 – Sep 22, 2026" — the range WITHOUT the duration the
  // header chip already carries (the same de-duplication the receipt
  // line below makes, Q-1685).
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

  function firstLine(text) {
    var s = String(text == null ? "" : text).split("\n")[0];
    return s.length > 120 ? s.slice(0, 119) + "…" : s;
  }

  // ── Render ────────────────────────────────────────────────────────
  var lastEnv = null;
  var lastMode = null;
  var chartEl = null;
  var chartPts = null;
  var chartW = -1;

  function drawIfNeeded(width) {
    if (!chartEl || !chartPts) return;
    var w = Math.floor(width || chartEl.clientWidth || 0);
    if (w <= 0 || w === chartW) return;
    chartW = w;
    drawChart(chartEl, chartPts, w, H.displayMode() === "fullscreen");
    H.notifySize();
  }

  /**
   * The evidence layout: header, tiles, receipt line, chart, notes.
   *
   * `body` is where it is drawn — `#card-body` for an `evidence` view,
   * and the receipt's own in-place container when the row is opened.
   * The header is drawn only into the card head, so an opened receipt
   * (which already has its row above) passes `headless`.
   */
  function renderEvidence(env, body, headless) {
    body.textContent = "";
    chartEl = null;
    chartPts = null;
    chartW = -1;
    var full = H.displayMode() === "fullscreen";
    lastMode = H.displayMode();

    // `view.metrics` is the one owner of metric key names and signs
    // when the envelope carries a view (§2.1). Older summarize
    // envelopes carry only `summary_metrics`, which is why both key
    // spellings are still picked below.
    var v = viewOf(env);
    var m = (v && v.metrics) || env.summary_metrics || {};
    var status = String((v && v.status) || env.status || "").toLowerCase();
    var completed = status === "completed" || status === "succeeded";
    // `view.window` is the one window owner (spec 03 §2.5, R-3); `period`
    // is an older envelope's fallback. `last_bar` rides along so the range
    // below ends on the last covered day, never the exclusive end.
    var period =
      v && v.window
        ? {
            start_date: v.window.start,
            end_date: v.window.end,
            last_bar: v.window.last_bar,
          }
        : env.period || {};

    // Header: Keel · name · version · state · how long the window is.
    if (!headless) {
      H.header({
        name: (v && v.name) || env.strategy_name || null,
        version:
          v && v.version != null
            ? v.version
            : env.sequence_number != null
              ? env.sequence_number
              : null,
        state: status || null,
        stateTone: STATE_TONE[status] || null,
        when: F.age(period.start_date, period.end_date),
      });
    }

    // Four tiles, the app's four; the rest one tap away (all of them in
    // fullscreen). Order and labels: the server's `view.tiles` (Q-1787).
    var entries = tileList(v, m).map(function (t) {
      return [t.label, tileDisplay(t), t.short_label || null];
    });
    var carry = pick(m, ["carry", "funding_attribution"]);
    if (carry != null) entries.push(["Carry", F.money(carry)]);
    body.appendChild(H.tiles(entries, { inline: full ? entries.length : 4 }));

    // The receipt: which window these numbers cover and what they are
    // net of. No ids, no engine, no run id, no raw stamp.
    //
    // ONE owner for the range text, and it is H.fmtPeriod — which keeps
    // BOTH years when the window spans them. The hand-rolled pair this
    // replaced passed withYear=false to the START unconditionally, so a
    // 2024-08-15 → 2026-09-21 run read "Aug 15 – Sep 21, 2026": five
    // weeks, printed under two years of tiles (Q-1685).
    //
    // fmtPeriod returns "<range> · <duration>", and the duration is the
    // same string the header chip above already carries (F.age is the
    // same fmtDuration call). Drop that repeat — matched as the exact
    // suffix the chip prints, so a range can never be truncated by this;
    // should the two ever disagree, nothing is removed.
    var span = H.fmtWindow({
      start: period.start_date,
      end: period.end_date,
      last_bar: period.last_bar,
    });
    var chip = F.age(period.start_date, period.end_date);
    if (span && chip) {
      var tail = " · " + chip;
      if (span.length > tail.length && span.slice(-tail.length) === tail) {
        span = span.slice(0, -tail.length);
      }
    }
    var netOf =
      v && Array.isArray(v.net_of) && v.net_of.length
        ? "net of " +
          (v.net_of.length > 1
            ? v.net_of.slice(0, -1).join(", ") +
              " and " +
              v.net_of[v.net_of.length - 1]
            : v.net_of[0])
        : "net of fees, slippage and carry";
    var receipt = H.receipt([span, completed ? netOf : null]);
    if (receipt) body.appendChild(receipt);
    // The platform moved the requested window (Q-1842): said beside the
    // range it changed, in the server's words — "Start moved from Jan 1,
    // 2024 to Jul 27, 2024 — …". Before this the card showed the range
    // that ran and nothing said it was not the range asked for.
    if (v && typeof v.window_note === "string" && v.window_note)
      body.appendChild(H.el("p", "note", v.window_note));
    // How much of the window held positions (spec 07 §5): the app's line,
    // "Held positions on 6 of 25 bars · first position Sep 20, 2026".
    // Absent on runs before the worker stamped it — nothing is drawn.
    if (v && typeof v.exposure_line === "string" && v.exposure_line)
      body.appendChild(H.el("p", "note", v.exposure_line));
    // What the counts are when the run's era left one unrecorded — the
    // server's sentence (Q-2122, spec 01 §6.2); a count it did not record
    // draws "—" in its tile.
    if (v && typeof v.count_basis === "string" && v.count_basis)
      body.appendChild(H.el("p", "note", v.count_basis));
    // The part of the run the caller asked about (summarize start/end/
    // capital), in keel-api's words, beside the whole run's tiles.
    if (env.slice && typeof env.slice.summary === "string" && env.slice.summary)
      body.appendChild(H.el("p", "note", env.slice.summary));

    // Chart
    chartPts = readCurve(env.curve);
    if (chartPts) coverDays(chartPts, period);
    if (chartPts) {
      chartEl = H.el("div", "chart");
      body.appendChild(chartEl);
      drawIfNeeded(chartEl.clientWidth);
      if (chartW < 0) {
        // Not laid out yet (hidden host frame) — retry on the next frame.
        setTimeout(function () {
          drawIfNeeded(chartEl && (chartEl.clientWidth || 600));
        }, 0);
      }
    } else if (completed) {
      body.appendChild(
        H.el(
          "p",
          "note",
          "Chart unavailable for this run — open the tearsheet for the full curve.",
        ),
      );
    }

    // Fullscreen carries what inline cannot — and nothing else. What
    // used to sit here carried nothing (Q-1685 / review F-6): a "Window"
    // row restating the receipt eighteen lines above it (and, until the
    // fix above, CONTRADICTING it), a "Cost model" row restating the
    // receipt's "net of fees, slippage and carry", and a "Curve · 240 of
    // 767 points, extremes preserved" row describing our downsampler,
    // which a reader cannot act on and did not ask for. Fullscreen's
    // real payload is the whole tile set inline and the larger chart.

    // What the run has to SAY beside its numbers. The worker's machine
    // code (SYMBOL_DELISTED, ...) never renders — only its message.
    var notes = env.notes && typeof env.notes === "object" ? env.notes : null;
    if (notes) {
      if (notes.non_result && notes.non_result.message) {
        body.appendChild(H.el("p", "note", H.prose(notes.non_result.message)));
      }
      var assets = Array.isArray(notes.assets) ? notes.assets : [];
      if (assets.length) {
        body.appendChild(
          H.list(
            assets.map(function (n) {
              return n && H.prose(n.message);
            }),
            { as: "notes", cap: full ? assets.length : 1 },
          ),
        );
      }
    }
    var errText = H.prose((v && v.error) || env.error_message);
    if (errText) {
      body.appendChild(H.el("p", "note error", errText));
    }
    // A server-composed sentence for under the result (`render.note`:
    // today the full profile's good-result line — spec 02 §2.4 retired the
    // `nudge` field this used to read; the listed profile carries none).
    var note = env.render && env.render.note;
    if (note) {
      body.appendChild(H.el("p", "note", H.prose(note)));
    }
    // Last, just above the action row: what kind of numbers these are
    // (S-6). Only a completed run has performance to qualify; an opened
    // receipt draws it too, because opening it draws the full result.
    if (completed) body.appendChild(H.historicalNote());
  }

  // ── The receipt (BUILD §4.1) ──────────────────────────────────────

  /** What a superseded card says — short, so the name keeps its width. */
  function supersededText() {
    return typeof H.supersededLabel === "function" ? H.supersededLabel() : null;
  }

  /**
   * Whether a completed run covered a DELIBERATE sub-window rather than the
   * default "all the history there is, up to now" (Q-1880). Read from the
   * served `window` object only:
   *   - the caller named a start that the platform did not have to move
   *     (no `start_reason_code`) — a chosen start, e.g. a second half; or
   *   - the last covered day is more than two days before the run completed
   *     — a chosen end, e.g. a first half.
   * An envelope without the served object is never called a sub-window.
   */
  var SUB_WINDOW_END_SLACK_MS = 2 * 86400000;
  function isSubWindow(env) {
    var w = env && env.window;
    if (!w || typeof w !== "object") return false;
    var req = w.requested && typeof w.requested === "object" ? w.requested : {};
    if (req.start && !w.start_reason_code) return true;
    var ran = w.ran && typeof w.ran === "object" ? w.ran : {};
    var last = Date.parse(ran.last_bar);
    var done = Date.parse(env.completed_at);
    if (isNaN(last) || isNaN(done)) return false;
    return done - last > SUB_WINDOW_END_SLACK_MS;
  }

  function receiptSpec(env, v) {
    var m = v.metrics || {};
    var status = String(v.status || "").toLowerCase();
    var spec = {
      name: v.name,
      version: v.version,
      state: status || null,
      stateTone: STATE_TONE[status] || null,
      url: heroUrl(env),
      chips: [],
    };
    spec.superseded = supersededText();
    // A moved start is part of what the numbers mean (Q-1842), so even the
    // one-line receipt says it: "Start moved Jan 1, 2024 → Jul 27, 2024".
    // The full sentence (and its reason) is the opened result's note.
    var adj =
      env.window_adjusted && typeof env.window_adjusted === "object"
        ? env.window_adjusted
        : null;
    if (
      adj &&
      adj.requested_start &&
      adj.effective_start &&
      String(adj.requested_start) !== String(adj.effective_start)
    )
      spec.note =
        "Start moved " +
        H.fmtDate(adj.requested_start, true) +
        " → " +
        H.fmtDate(adj.effective_start, true);
    if (status === "completed" || status === "succeeded") {
      // Three numbers, unlabelled because their order is fixed; the
      // row's aria-label names them (the adapter does that).
      spec.nums = {
        sharpe: m.sharpe,
        ret: m.total_return_pct,
        dd: m.max_drawdown_pct,
      };
      // A sub-window run says which window (Q-1880): two half-period
      // receipts of one version read identically otherwise.
      if (isSubWindow(env)) spec.window = windowText(v.window);
      spec.disclosure = {
        label: "Show result",
        build: function (target) {
          renderEvidence(env, target, true);
        },
      };
    } else if (status === "queued" || status === "running") {
      // A snapshot: no numbers, no skeleton, no disclosure — the row is
      // complete, and no second result ever updates it.
      spec.window = windowText(v.window);
    } else if (status === "failed") {
      spec.err = firstLine(H.prose(v.error));
      spec.disclosure = {
        label: "Show error",
        build: function (target) {
          target.textContent = "";
          target.appendChild(
            H.el("p", "note error", H.prose(v.error) || "No message."),
          );
          var notes =
            env.notes && typeof env.notes === "object" ? env.notes : null;
          if (notes && notes.non_result && notes.non_result.message) {
            target.appendChild(
              H.el("p", "note", H.prose(notes.non_result.message)),
            );
          }
        },
      };
    }
    // cancelled: the chip and nothing else.
    return spec;
  }

  function heroUrl(env) {
    if (!env) return null;
    if (env.hero_url) return env.hero_url;
    if (env.share_url) return env.share_url;
    if (env.view && typeof env.view.url_line === "string") {
      var m = env.view.url_line.match(/https?:\/\/\S+/);
      if (m) return m[0];
    }
    return null;
  }

  /** The honest error state: what failed, and what it said. */
  function renderErrorLine(body, env, err) {
    body.textContent = "";
    if (document.body) {
      document.body.setAttribute("data-size", "receipt");
      document.body.setAttribute("data-owns-link", "0");
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
    var row = H.el("div", "err-line");
    if (H.wordmark()) row.appendChild(H.el("span", "brand", "Keel"));
    var v = viewOf(env);
    if (v && v.name) {
      var nm = H.el("span", "rr-name", v.name);
      nm.title = v.name;
      row.appendChild(nm);
    }
    if (v && v.version != null)
      row.appendChild(H.el("span", "chip", "v" + v.version));
    // The card owns a lead only when it can support it (Q-1713); when it
    // does, the server's own failure preamble is dropped so the line never
    // reads "not submitted — Cannot backtest — …" (Q-1800).
    row.appendChild(
      H.el(
        "span",
        "msg",
        err.lead ? err.lead + H.withoutFailureLead(err.message) : err.message,
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
    var err = errorOf(env);
    if (err) {
      renderErrorLine(body, env, err);
      return;
    }
    var v = viewOf(env);
    var full = H.displayMode() === "fullscreen";
    // Fullscreen renders the evidence layout whatever the receipt
    // state is — the whole point of Expand.
    if (v && v.size === "receipt" && !full) {
      body.textContent = "";
      chartEl = null;
      chartPts = null;
      chartW = -1;
      lastMode = H.displayMode();
      body.appendChild(H.receiptRow(receiptSpec(env, v)));
      return;
    }
    if (document.body) document.body.removeAttribute("data-size");
    renderEvidence(env, body, false);
  }

  H.ready(function (env) {
    lastEnv = env || {};
    render(lastEnv);
  });

  // A younger sibling for the same object appeared: fall back to the
  // receipt with its chip (BUILD §2.9). The card keeps its link and its
  // in-place open — it stops being the biggest thing on screen, it does
  // not vanish.
  if (typeof H.onSuperseded === "function") {
    H.onSuperseded(function () {
      if (!lastEnv) return;
      var v = viewOf(lastEnv);
      if (v) v.size = "receipt";
      render(lastEnv);
      H.notifySize();
    });
  }

  // Registered ONCE, outside the result listener: a second tool result
  // re-renders the card (the adapter no longer latches), and a listener
  // added per render would multiply with it.
  H.onResize(function (width) {
    if (!lastEnv) return;
    if (H.displayMode() !== lastMode) render(lastEnv);
    else drawIfNeeded(chartEl ? chartEl.clientWidth || width : width);
  });
})();
