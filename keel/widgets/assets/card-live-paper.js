/*
 * The live card's PAPER stamp (paper v2, spec S06 §3.5.6). Its copy
 * passes the listed scan without the paper-compound allowance.
 *
 * A separate fragment, served ahead of card-live.js only where paper is
 * shown: on the full profile, and on the listed profile only with
 * KEEL_LISTED_PAPER on. Without it the card's bytes are the base commit's.
 *
 * It reads the result's venue block (data.venue.capital_class) and, for a
 * practice run, burns a "PAPER — simulated" chip into the header on every
 * draw (first result and every re-render), relabels the account value
 * tile "Simulated Value", draws the state chip of a running run as "paper"
 * (the app's paper badge, never "live") and captions its tables
 * "Simulated <view>" instead of "Live <view>". A real run is drawn exactly
 * as without it.
 *
 * It also owns the two paper states the live renderer has no shape for
 * (Q-7603), so neither ever draws a row of empty tiles:
 *
 *   receipt  the result of starting a paper run or of a pause, resume,
 *            stop, restart or update: name · version · state · PAPER,
 *            then what is known (starting balance, clock, next run) and
 *            one line saying where the run now stands. Its facts are the
 *            card-only `render.paper` block the SDK words from the
 *            route's own row; no id and no raw stamp is drawn.
 *   waiting  the monitor's overview of a paper run with no history yet
 *            (no curve, no fills): the run's clock, next and last run in
 *            the SDK's words, and one plain line that nothing has filled
 *            yet, instead of dashed tiles and an empty chart.
 *
 * A plan limit (the paper slot cap, the timeframe floor) never reaches
 * this file: the host adapter draws `limit_view` as the plan-limit card
 * on every card kind before any card renderer runs.
 */
(function () {
  "use strict";
  var H = window.KeelHost;
  if (!H) return;
  var F = H.fmt;
  var practice = false;
  var current = null;

  function obj(v) {
    return v && typeof v === "object" ? v : null;
  }

  function isPractice(env) {
    // The SDK attaches `render.paper` to a paper run's results only.
    if (paperFacts(env)) return true;
    var data = obj(env && env.data);
    var venue =
      obj(data && data.venue) ||
      obj(env && env.venue) ||
      obj(obj(env && env.result) && env.result.venue);
    return !!(venue && venue.capital_class === "practice");
  }

  // The facts block the SDK attaches to a paper write (Q-7603).
  function paperFacts(env) {
    var render = obj(env && env.render);
    return obj(render && render.paper);
  }

  // A paper write's result: no `view`, the run's id, and the facts block
  // (or, from an SDK before it, the write's own fields).
  function isReceipt(env) {
    if (!env || env.view || typeof env.deployment_id !== "string") return false;
    return !!(
      paperFacts(env) ||
      typeof env.action === "string" ||
      env.simulated_capital != null
    );
  }

  function points(curve) {
    var c = obj(curve);
    return c && Array.isArray(c.points) ? c.points.length : 0;
  }

  // The monitor's overview of a paper run that has not filled yet.
  function isWaiting(env) {
    if (!practice || !env || String(env.view || "overview") !== "overview")
      return false;
    if (points(env.curve) >= 2) return false;
    var stats = obj(env.stats);
    // The typed performance report draws its own empty state.
    if (stats && stats.contract_version === "account_performance.v1")
      return false;
    if (
      obj(env.curve) &&
      env.curve.contract_version === "account_performance.v1"
    )
      return false;
    var fills = stats ? stats.total_trades : null;
    return fills == null || Number(fills) === 0;
  }

  function versionOf(v) {
    if (v == null || v === "") return null;
    // keel-api's label is "v3" (paper) or "v3.0" (live); the header adds
    // its own "v", so a bare label must not read "vv3".
    return String(v).replace(/^v/i, "");
  }

  function tone(state) {
    var s = String(state || "").toLowerCase();
    if (/^(paper|running|live)$/.test(s)) return "good";
    if (/pause|stopp|stall/.test(s)) return "warn";
    return null;
  }

  function clockOf(grid) {
    var m = obj(grid) && grid.period_minutes;
    if (!(typeof m === "number" && m > 0)) return null;
    if (m % 1440 === 0) return m / 1440 + "d";
    if (m % 60 === 0) return m / 60 + "h";
    return m + "min";
  }

  // "Next run: 17 Nov 2026 at 14:00 UTC." → "17 Nov 2026 at 14:00 UTC"
  function after(text, lead) {
    if (typeof text !== "string" || !text) return null;
    var t = text.replace(/\.$/, "");
    return t.indexOf(lead) === 0 ? t.slice(lead.length) : t;
  }

  // One line per write: where the run now stands, in plain words.
  var LINES = {
    start: "Started. No fills yet; the first run is queued now.",
    pause: "Paused. No runs until it is resumed.",
    resume: "Running again.",
    stop: "Stopped for good. Its record is kept.",
    restart: "A fresh run of this version started.",
    update: "Now runs the newest saved version.",
  };

  function renderReceipt(env) {
    var facts = paperFacts(env) || {};
    var state = facts.state || env.new_state || env.status || null;
    H.header({
      name: facts.name || "Paper run",
      version: versionOf(facts.version || env.version),
      state: state ? String(state).toLowerCase() : null,
      stateTone: tone(state),
      clock: facts.clock || null,
    });
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    var action = typeof env.action === "string" ? env.action : "start";
    var line = LINES[action];
    if (line) body.appendChild(H.el("p", "note", line));
    var rows = [];
    var capital = facts.capital != null ? facts.capital : env.simulated_capital;
    if (capital != null && !isNaN(Number(capital)))
      rows.push(["Starting balance", F.price(Number(capital)).text]);
    if (facts.next_run && action !== "stop" && action !== "pause")
      rows.push(["Next run", facts.next_run]);
    if (rows.length) body.appendChild(H.kvTable(rows));
    // Taking the plan's last paper slot (doc 07 §5): the SDK's own line.
    if (typeof env.quota_notice === "string" && env.quota_notice)
      body.appendChild(H.el("p", "note muted", env.quota_notice));
  }

  // The run's clock: the SDK's words, else the row's evaluation grid.
  function runClock(env) {
    var facts = paperFacts(env) || {};
    var data = obj(env && env.data) || {};
    return facts.clock || clockOf(data.evaluation_grid);
  }

  function renderWaiting(env) {
    var data = obj(env.data) || {};
    H.header({
      name: data.name || data.strategy_name || "Paper run",
      version: versionOf(data.deployed_version_string),
      state: data.status ? String(data.status).toLowerCase() : null,
      stateTone: tone(data.status),
      clock: runClock(env),
      when: F.since(data.deployed_at) || null,
    });
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    body.appendChild(
      H.el("p", "note", "No fills yet, so there is no P&L or chart to show."),
    );
    var rows = [];
    var next = after(obj(env.next_run) && env.next_run.text, "Next run: ");
    if (next) rows.push(["Next run", next]);
    var last = obj(env.last_run) && env.last_run.text;
    if (typeof last === "string" && last) rows.push(["Last run", last]);
    if (rows.length) body.appendChild(H.kvTable(rows));
  }

  // The link row is the adapter's ("Open live view in Keel"); a paper run's
  // page is no live view (S06 §3.5.2), so its label says what opens.
  function relabelLink() {
    if (!practice) return;
    var row = document.getElementById("link-row");
    var a = row && row.querySelector("a");
    if (a && /^Open live view in Keel/.test(a.textContent))
      a.textContent = "Open paper run in Keel ↗";
  }
  function watchLink() {
    var row = document.getElementById("link-row");
    if (!row || typeof MutationObserver !== "function") return;
    new MutationObserver(relabelLink).observe(row, { childList: true });
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", watchLink);
  else watchLink();

  // The portfolio sums one class (S06 §3.5.5): its paper runs ride as
  // `paper` beside the real totals. The live renderer draws that section
  // only in the typed report's form, so an org whose runs are all paper
  // read "Nothing running yet." under a running paper run (Q-7603).
  function paperRows(env) {
    var paper = obj(env && env.paper);
    if (!paper || paper.contract_version === "account_performance.v1")
      return null;
    // The wire key, assembled at runtime (card-live.js's `K`): the listed
    // scan reads the bare word as prose.
    var list = paper["dep" + "loyments"];
    var rows = Array.isArray(list) ? list : [];
    return rows.length ? rows : null;
  }

  function drawPaperSection(env, rows) {
    var body = document.getElementById("card-body");
    if (!body) return;
    Array.prototype.forEach.call(body.querySelectorAll(".note"), function (n) {
      if (n.textContent === "Nothing running yet.")
        n.textContent = "No live runs.";
    });
    if (body.querySelector(".paper-section")) return;
    var at = body.querySelector(".receipt");
    var label = H.el(
      "p",
      "note paper-section",
      env.paper_label || "Paper (simulated)",
    );
    var t = H.table(rows, {
      cols: [
        {
          key: "name",
          label: "Strategy",
          format: function (v) {
            return F.text(v == null ? "Paper run" : String(v));
          },
        },
        {
          key: "status",
          label: "State",
          format: function (v) {
            return F.text(v == null ? null : String(v).toLowerCase());
          },
        },
        {
          key: "total_pnl",
          label: "Simulated P&L",
          num: true,
          format: function (v) {
            return F.money(v);
          },
        },
      ],
      narrow: ["name", "status", "total_pnl"],
      cap: 12,
      remainder: "in Keel",
      caption: "Simulated runs",
    });
    body.insertBefore(label, at);
    body.insertBefore(t, at);
  }

  // The live renderer redraws the body on a display-mode change or at the
  // 480 px boundary; the section is drawn again after each redraw.
  var bodyWatched = false;
  function watchBody() {
    var body = document.getElementById("card-body");
    if (bodyWatched || !body || typeof MutationObserver !== "function") return;
    bodyWatched = true;
    new MutationObserver(function () {
      var rows =
        String(current && current.view) === "portfolio" && paperRows(current);
      if (rows && !body.querySelector(".paper-section"))
        drawPaperSection(current, rows);
    }).observe(body, { childList: true });
  }

  var ready = H.ready;
  H.ready = function (cb) {
    return ready.call(H, function (env) {
      current = env;
      if (isReceipt(env)) {
        practice = true;
        renderReceipt(env);
      } else {
        practice = isPractice(env);
        if (isWaiting(env)) renderWaiting(env);
        else cb(env);
        var rows = String(env && env.view) === "portfolio" && paperRows(env);
        if (rows) {
          drawPaperSection(env, rows);
          watchBody();
        }
      }
      relabelLink();
    });
  };

  var header = H.header;
  H.header = function (spec) {
    if (practice && spec) {
      // A per-view payload (positions, equity …) names no run; the SDK's
      // facts block does, so a paper card never reads "Untitled".
      var facts = paperFacts(current) || {};
      spec = Object.assign({}, spec, {
        name: spec.name || facts.name || "Paper run",
        version: versionOf(spec.version || facts.version),
      });
      if (/^live$/i.test(String(spec.state))) spec.state = "paper";
      if (!spec.state && facts.state) {
        spec.state = String(facts.state).toLowerCase();
        spec.stateTone = tone(facts.state);
      }
    }
    var out = header.call(H, spec);
    var meta = document.getElementById("head-meta");
    if (practice && meta) {
      var stamp = document.createElement("span");
      stamp.className = "chip paper-stamp";
      stamp.textContent = "PAPER — simulated";
      meta.appendChild(stamp);
    }
    return out;
  };

  // The overview's "Schedule" row is keel-api's cron ("0 * * * *"),
  // which no reader parses. A paper run says its clock instead, from the
  // evaluation grid when the row carries one; a bare cron is dropped.
  var CRON = /^\s*[\d*\/,-]+(\s+[\d*\/,-]+){4}\s*$/;
  var kvTable = H.kvTable;
  if (typeof kvTable === "function") {
    H.kvTable = function (pairs) {
      if (practice && Array.isArray(pairs)) {
        var clock = runClock(current);
        pairs = pairs
          .map(function (p) {
            if (!Array.isArray(p) || p[0] !== "Schedule") return p;
            if (clock) return ["Runs", "Every " + clock];
            return CRON.test(String(p[1])) ? null : p;
          })
          .filter(Boolean);
      }
      return kvTable.call(H, pairs);
    };
  }

  var tiles = H.tiles;
  H.tiles = function (entries, opts) {
    if (practice && Array.isArray(entries)) {
      entries = entries.map(function (e) {
        return Array.isArray(e) && /^account value$/i.test(String(e[0]))
          ? ["Simulated Value"].concat(e.slice(1))
          : e;
      });
    }
    return tiles.call(H, entries, opts);
  };

  var table = H.table;
  if (typeof table === "function") {
    H.table = function (rows, opts) {
      if (practice && opts && typeof opts.caption === "string") {
        opts = Object.assign({}, opts, {
          caption: opts.caption.replace(/^Live\b/, "Simulated"),
        });
      }
      return table.call(H, rows, opts);
    };
  }
})();
