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
 */
(function () {
  "use strict";
  var H = window.KeelHost;
  if (!H) return;
  var practice = false;

  function isPractice(env) {
    var data = env && typeof env.data === "object" ? env.data : null;
    var venue = data && typeof data.venue === "object" ? data.venue : null;
    return !!(venue && venue.capital_class === "practice");
  }

  var ready = H.ready;
  H.ready = function (cb) {
    return ready.call(H, function (env) {
      practice = isPractice(env);
      cb(env);
    });
  };

  var header = H.header;
  H.header = function (spec) {
    if (practice && spec && /^live$/i.test(String(spec.state))) {
      spec = Object.assign({}, spec, { state: "paper" });
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
