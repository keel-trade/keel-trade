/* Deploy-preflight card — renders keel_live_deploy's PREVIEW envelope.
 * FULL/UNLISTED PROFILE ONLY (spec 06 R2): this file is never composed
 * into a listed-profile bundle and the policy scan proves its absence.
 *
 * Render-only (D2.2, Q-1505): the strategy's name + schedule from the
 * server preview (preview.raw), server-computed sizing (deploy_intent.
 * suggested_config), the preview's estimated slippage/fees and universe
 * size as tiles, the confirmation window, and the link row (handoff
 * URL when minted). No ids. The card never computes sizing and never
 * confirms the request itself — confirmation stays in the tool flow.
 *
 * Numbers come from the one formatter (Q-1629/Q-1634) — the local
 * `money()` copy is gone. The confirmation deadline renders as a
 * relative window, never a raw UTC stamp (Q-1630).
 */
(function () {
  "use strict";
  var H = window.KeelHost;
  var F = H.fmt;

  // Fractions (0.001) read as 0.10%; values already in percent pass through.
  function asPct(v) {
    if (v == null || isNaN(Number(v))) return null;
    var n = Number(v);
    return Math.abs(n) < 1 ? n * 100 : n;
  }

  function sum(a, b) {
    if (a == null && b == null) return null;
    return (a == null ? 0 : a) + (b == null ? 0 : b);
  }

  function warningText(w) {
    if (!w) return null;
    if (typeof w === "string") return w;
    // The machine code is never the prose — a warning with no message
    // is counted, not stringified (Q-1630).
    // Server prose, so it carries the server's ids (Q-1712).
    return typeof w.message === "string"
      ? H.prose(w.message)
      : typeof w.reason === "string"
        ? H.prose(w.reason)
        : null;
  }

  var lastEnv = null;
  var lastMode = null;

  function render(env) {
    var body = document.getElementById("card-body");
    body.textContent = "";
    var full = H.displayMode() === "fullscreen";
    lastMode = H.displayMode();

    var preview = env.preview || {};
    var raw = preview.raw && typeof preview.raw === "object" ? preview.raw : {};
    var intent = env.deploy_intent || {};
    var sizing = intent.suggested_config || {};

    var clock = raw.timeframe
      ? String(raw.timeframe) +
        (raw.bar_offset ? " · offset " + raw.bar_offset : "")
      : null;
    H.header({
      name: raw.strategy_name || null,
      version: raw.version != null ? raw.version : null,
      state: "preview",
      clock: clock,
    });

    var slip = asPct(preview.est_slippage);
    var fees = asPct(preview.est_fees);
    var entries = [
      ["Sizing", F.price(sizing.sizing_usd, { dp: 0 })],
      ["Expected DD", F.drawdownMoney(sizing.expected_drawdown_usd, { dp: 0 })],
      ["Est. cost", F.rate(sum(slip, fees), { dp: 2 })],
      ["Assets", F.count(raw.universe_size)],
      ["Est. slippage", F.rate(slip, { dp: 2 })],
      ["Est. fees", F.rate(fees, { dp: 2 })],
    ];
    body.appendChild(H.tiles(entries, { inline: full ? entries.length : 4 }));

    var schedule =
      raw.schedule_description ||
      preview.schedule ||
      preview.derived_schedule ||
      raw.derived_schedule ||
      null;
    var left = F.within(env.confirmation_expires_at);
    var receipt = H.receipt([
      schedule,
      "estimated cost, not a quote",
      left == null
        ? null
        : left === "window closed"
          ? "confirmation window closed"
          : "confirm " + left,
    ]);
    if (receipt) body.appendChild(receipt);

    var facts = [];
    if (sizing.sizing_basis)
      facts.push([
        "Sizing basis",
        String(sizing.sizing_basis).replace(/_/g, " "),
      ]);
    if (raw.tranche_schedule_description)
      facts.push(["Tranches", raw.tranche_schedule_description]);
    if (facts.length) body.appendChild(H.kvTable(facts));

    var uw = raw.universe_warnings;
    if (Array.isArray(uw) && uw.length) {
      var first = warningText(uw[0]);
      var head =
        uw.length +
        (uw.length === 1 ? " universe warning" : " universe warnings");
      body.appendChild(
        H.el(
          "p",
          "note error",
          first ? head + ": " + first : head + " — open in Keel.",
        ),
      );
      if (full && uw.length > 1) {
        uw.slice(1).forEach(function (w) {
          var t = warningText(w);
          if (t) body.appendChild(H.el("p", "note error", t));
        });
      }
    }
    if (env.sync_note)
      body.appendChild(H.el("p", "note", H.prose(env.sync_note)));
    body.appendChild(
      H.el(
        "p",
        "note",
        "Preview only — nothing goes live until you confirm in the tool flow (or continue in Keel below).",
      ),
    );
  }

  H.ready(function (env) {
    lastEnv = env || {};
    render(lastEnv);
  });

  H.onResize(function () {
    if (!lastEnv) return;
    if (H.displayMode() !== lastMode) render(lastEnv);
  });
})();
